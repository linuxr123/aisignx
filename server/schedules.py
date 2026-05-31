from contextlib import contextmanager

from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify, session
from flask_login import login_required, current_user
from datetime import datetime
from models import (db, Schedule, Playlist, Display, DisplayGroup,
                    EmergencyBroadcast, EmergencyTemplate, Domain, UserDomainRole)
from utils import parse_date, parse_time, api_auth_required
from permissions import require_permission
from audit import audit
from logging_config import logger
from tenant_filter import bypass_tenant_filter, current_domain_id, set_current_domain_id

schedules_bp = Blueprint('schedules', __name__)


def _is_superadmin():
    return getattr(current_user, 'is_superadmin', False)


def _can_admin_domain(domain_id):
    """Superadmin or domain.admin for the tenant."""
    if _is_superadmin():
        return True
    if domain_id is None:
        return False
    from permissions import has_permission
    return has_permission(current_user, 'domain.admin', domain_id=domain_id)


def _can_use_emergency_in_domain(domain_id):
    """May view/trigger emergencies in a tenant."""
    if _is_superadmin():
        return True
    if domain_id is None:
        return False
    from permissions import has_permission
    return (has_permission(current_user, 'emergency.use', domain_id=domain_id)
            or has_permission(current_user, 'emergency.manage', domain_id=domain_id)
            or has_permission(current_user, 'domain.admin', domain_id=domain_id))


def _can_manage_emergency_templates(domain_id=None):
    """May create/edit/delete saved emergency templates (tenant admins)."""
    if _is_superadmin():
        return True
    if domain_id is None:
        domain_id = _session_domain_id() or current_domain_id()
    if domain_id is None:
        return False
    from permissions import has_permission
    if has_permission(current_user, 'domain.admin', domain_id=domain_id):
        return True
    if has_permission(current_user, 'emergency.manage', domain_id=domain_id):
        return True
    # Custom tenant-admin roles that include schedule + emergency but not domain.admin key
    return (has_permission(current_user, 'schedule.edit', domain_id=domain_id)
            and has_permission(current_user, 'emergency.use', domain_id=domain_id))


def _session_domain_id():
    """Active tenant from the sidebar switcher (session cookie)."""
    raw = session.get('current_domain_id')
    if raw is None:
        return None
    try:
        did = int(raw)
    except (TypeError, ValueError):
        return None
    return did if did > 0 else None


@contextmanager
def _emergency_tenant_context(domain_id):
    """Align ORM tenant context and allow cross-tenant writes (superadmin tooling)."""
    prev = current_domain_id()
    set_current_domain_id(domain_id)
    try:
        with bypass_tenant_filter():
            yield
    finally:
        set_current_domain_id(prev)


def _fallback_emergency_domain_id():
    """If session has no active tenant, use the sole domain the user may access."""
    if _is_superadmin():
        return None
    with bypass_tenant_filter():
        ids = sorted({r.domain_id for r in UserDomainRole.query.filter_by(
            user_id=current_user.id).all()})
    eligible = [d for d in ids if _can_use_emergency_in_domain(d)]
    return eligible[0] if len(eligible) == 1 else None


def _resolve_emergency_domain_id(data=None):
    """Resolve tenant for emergency APIs from query/body or session context.

    Returns (domain_id, None) or (None, error_response_tuple).
    Superadmins must pass domain_id when no session tenant is set.
    """
    raw = None
    if data is not None and 'domain_id' in data:
        raw = data.get('domain_id')
    if raw is None:
        raw = request.args.get('domain_id')
    if raw is not None and str(raw).strip() != '':
        try:
            did = int(raw)
        except (TypeError, ValueError):
            return None, (jsonify({'status': 'error',
                                   'message': 'invalid domain_id'}), 400)
        if did <= 0:
            return None, (jsonify({'status': 'error',
                                   'message': 'domain_id required'}), 400)
        if not _can_use_emergency_in_domain(did):
            return None, (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
        return did, None

    did = _session_domain_id()
    if did is None:
        did = current_domain_id()
    if did is None:
        did = _fallback_emergency_domain_id()
    if did is not None and _can_use_emergency_in_domain(did):
        return did, None
    if _is_superadmin():
        return None, (jsonify({'status': 'error',
                               'message': 'domain_id required'}), 400)
    return None, (jsonify({'status': 'error', 'message': 'forbidden'}), 403)


def _guard_emergency_use(domain_id):
    """403 if the principal may not use emergencies in this tenant."""
    if domain_id is None:
        return jsonify({'status': 'error',
                        'message': 'Select Active tenant in the sidebar'}), 400
    if not _can_use_emergency_in_domain(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden',
                        'permission': 'emergency.use'}), 403
    return None


def _template_to_dict(tmpl):
    """Serialize after commit without lazy-load surprises."""
    with bypass_tenant_filter():
        row = db.session.get(EmergencyTemplate, tmpl.id)
        return (row or tmpl).to_dict()


def _validate_emergency_target(target, domain_id):
    """Ensure display/group targets belong to the tenant."""
    if not target or target == 'all':
        return True, None
    if target.startswith('display:'):
        try:
            did = int(target.split(':', 1)[1])
        except (ValueError, IndexError):
            return False, 'invalid display target'
        with bypass_tenant_filter():
            row = db.session.get(Display, did)
        if row is None or row.domain_id != domain_id:
            return False, 'display not found in this tenant'
        return True, None
    if target.startswith('group:'):
        try:
            gid = int(target.split(':', 1)[1])
        except (ValueError, IndexError):
            return False, 'invalid group target'
        with bypass_tenant_filter():
            row = db.session.get(DisplayGroup, gid)
        if row is None or row.domain_id != domain_id:
            return False, 'group not found in this tenant'
        return True, None
    return False, 'invalid target'


def _get_emergency_broadcast(broadcast_id):
    """Load a broadcast if the requester may administer its tenant."""
    with bypass_tenant_filter():
        broadcast = db.session.get(EmergencyBroadcast, broadcast_id)
    if broadcast is None:
        return None, (jsonify({'status': 'error', 'message': 'not found'}), 404)
    if not _can_use_emergency_in_domain(broadcast.domain_id):
        return None, (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
    return broadcast, None


def _get_emergency_template(tmpl_id):
    with bypass_tenant_filter():
        tmpl = db.session.get(EmergencyTemplate, tmpl_id)
    if tmpl is None:
        return None, (jsonify({'status': 'error', 'message': 'template not found'}), 404)
    if not _can_use_emergency_in_domain(tmpl.domain_id):
        return None, (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
    return tmpl, None


def _forbidden_template_manage():
    return jsonify({
        'status': 'error',
        'message': 'forbidden',
        'permission': 'domain.admin or emergency.manage',
    }), 403

@schedules_bp.route('/schedules')
@login_required
@require_permission('schedule.read')
def schedules():
    from domains import domain_switcher_state
    schedules = Schedule.query.all()
    playlists = Playlist.query.all()
    displays = Display.query.all()
    groups = DisplayGroup.query.all()
    ds = domain_switcher_state()
    return render_template(
        'schedules.html',
        schedules=schedules,
        playlists=playlists,
        displays=displays,
        groups=groups,
        active_tenant_name=ds.get('current_name'),
        active_domain_id=ds.get('current_id'),
    )

@schedules_bp.route('/schedules/edit/<int:schedule_id>', methods=['GET', 'POST'])
@login_required
@require_permission('schedule.edit')
def edit_schedule(schedule_id):
    schedule = Schedule.query.get_or_404(schedule_id)
    playlists = Playlist.query.all()
    displays = Display.query.all()
    groups = DisplayGroup.query.all()

    if request.method == 'POST':
        schedule.name = request.form['name']
        schedule.playlist_id = int(request.form['playlist_id'])
        schedule.display_id = int(request.form.get('display_id', 0)) or None
        schedule.group_id = int(request.form.get('group_id', 0)) or None
        schedule.priority = int(request.form['priority'])
        schedule.is_active = 'is_active' in request.form

        schedule.start_date = request.form.get('start_date') or None
        schedule.end_date = request.form.get('end_date') or None
        schedule.start_time = request.form.get('start_time') or None
        schedule.end_time = request.form.get('end_time') or None
        schedule.days_of_week = request.form.get('days_of_week') or ""
        schedule.timezone = (request.form.get('timezone') or '').strip() or None

        db.session.commit()
        try:
            from display_player import notify_schedule_playlist_reload
            notify_schedule_playlist_reload(schedule)
        except Exception:
            pass
        flash('Schedule updated successfully.', 'success')
        return redirect(url_for('schedules.schedules'))

    return render_template(
        'schedules/edit.html',
        schedule=schedule,
        playlists=playlists,
        displays=displays,
        groups=groups
    )


# ---- API Endpoints ----
@schedules_bp.route('/api/schedules', methods=['GET'])
@api_auth_required(['schedule:read'])
@require_permission('schedule.read')
def api_get_schedules():
    schedules = Schedule.query.all()
    return jsonify({
        'status': 'success',
        'schedules': [s.to_dict() for s in schedules]
    })

@schedules_bp.route('/api/schedules', methods=['POST'])
@api_auth_required(['schedule:write'])
@require_permission('schedule.edit')
def api_create_schedule():
    data = request.json

    if not data or not data.get('name') or not data.get('playlist_id'):
        return jsonify({
            'status': 'error',
            'message': 'Schedule name and playlist ID are required'
        }), 400

    start_date = parse_date(data.get('start_date'))
    if data.get('start_date') and not start_date:
        return jsonify({'status': 'error', 'message': 'Invalid start_date format'}), 400

    end_date = parse_date(data.get('end_date'))
    if data.get('end_date') and not end_date:
        return jsonify({'status': 'error', 'message': 'Invalid end_date format'}), 400

    start_time = parse_time(data.get('start_time'))
    if data.get('start_time') and not start_time:
        return jsonify({'status': 'error', 'message': 'Invalid start_time format'}), 400

    end_time = parse_time(data.get('end_time'))
    if data.get('end_time') and not end_time:
        return jsonify({'status': 'error', 'message': 'Invalid end_time format'}), 400

    schedule = Schedule(
        name=data.get('name'),
        playlist_id=data.get('playlist_id'),
        display_id=data.get('display_id'),
        group_id=data.get('group_id'),
        start_date=start_date,
        end_date=end_date,
        start_time=start_time,
        end_time=end_time,
        days_of_week=data.get('days_of_week'),
        priority=data.get('priority', 0),
        is_active=data.get('is_active', True),
        timezone=(data.get('timezone') or '').strip() or None
            if isinstance(data.get('timezone'), str) else data.get('timezone'),
    )

    db.session.add(schedule)
    db.session.commit()
    audit('schedule.create', target_type='schedule', target_id=str(schedule.id),
          payload={'name': schedule.name, 'playlist_id': schedule.playlist_id,
                   'display_id': schedule.display_id, 'group_id': schedule.group_id,
                   'priority': schedule.priority})
    try:
        from display_player import notify_schedule_playlist_reload
        notify_schedule_playlist_reload(schedule)
    except Exception:
        pass

    return jsonify({
        'status': 'success',
        'message': 'Schedule created successfully',
        'schedule': schedule.to_dict()
    })

@schedules_bp.route('/api/schedules/<int:schedule_id>', methods=['GET'])
@api_auth_required(['schedule:read'])
@require_permission('schedule.read')
def api_get_schedule(schedule_id):
    schedule = Schedule.query.get_or_404(schedule_id)
    return jsonify({
        'status': 'success',
        'schedule': schedule.to_dict()
    })

@schedules_bp.route('/api/schedules/<int:schedule_id>', methods=['PUT'])
@api_auth_required(['schedule:write'])
@require_permission('schedule.edit')
def api_update_schedule(schedule_id):
    schedule = Schedule.query.get_or_404(schedule_id)
    data = request.json

    if data.get('name'):
        schedule.name = data.get('name')

    if data.get('playlist_id'):
        schedule.playlist_id = data.get('playlist_id')

    if 'display_id' in data:
        schedule.display_id = data.get('display_id')

    if 'group_id' in data:
        schedule.group_id = data.get('group_id')

    if 'start_date' in data:
        schedule.start_date = parse_date(data.get('start_date'))

    if 'end_date' in data:
        schedule.end_date = parse_date(data.get('end_date'))

    if 'start_time' in data:
        t = parse_time(data['start_time'])
        if t is None and data['start_time']:
            return jsonify({'status': 'error', 'message': 'Invalid start_time format'}), 400
        schedule.start_time = t

    if 'end_time' in data:
        t = parse_time(data['end_time'])
        if t is None and data['end_time']:
            return jsonify({'status': 'error', 'message': 'Invalid end_time format'}), 400
        schedule.end_time = t

    if 'days_of_week' in data:
        schedule.days_of_week = data.get('days_of_week')

    if 'priority' in data:
        schedule.priority = data.get('priority')

    if 'is_active' in data:
        schedule.is_active = data.get('is_active')

    if 'timezone' in data:
        tz = data.get('timezone')
        schedule.timezone = (tz or '').strip() or None if isinstance(tz, str) else (tz or None)

    db.session.commit()
    try:
        from display_player import notify_schedule_playlist_reload
        notify_schedule_playlist_reload(schedule)
    except Exception:
        pass

    return jsonify({
        'status': 'success',
        'message': 'Schedule updated successfully',
        'schedule': schedule.to_dict()
    })

@schedules_bp.route('/api/schedules/bulk-update', methods=['POST'])
@api_auth_required(['schedule:write'])
@require_permission('schedule.edit')
def api_bulk_update_schedules():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    changes = data.get('changes') or {}
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400
    if not isinstance(changes, dict) or not changes:
        return jsonify({'status': 'error', 'message': 'changes is empty'}), 400

    allowed = {}
    if 'is_active' in changes:
        allowed['is_active'] = bool(changes.get('is_active'))
    if 'priority' in changes:
        try:
            allowed['priority'] = int(changes.get('priority'))
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': 'priority must be an integer'}), 400
    if 'playlist_id' in changes:
        try:
            playlist_id = int(changes.get('playlist_id'))
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': 'playlist_id must be an integer'}), 400
        if not Playlist.query.get(playlist_id):
            return jsonify({'status': 'error',
                            'message': 'playlist not found'}), 404
        allowed['playlist_id'] = playlist_id
    if 'timezone' in changes:
        tz = changes.get('timezone')
        allowed['timezone'] = (tz or '').strip() or None if isinstance(tz, str) else (tz or None)

    if not allowed:
        return jsonify({'status': 'error',
                        'message': 'no recognized fields in changes'}), 400

    rows = Schedule.query.filter(Schedule.id.in_(ids)).all()
    found_ids = {s.id for s in rows}
    updated = 0
    for schedule in rows:
        row_changed = False
        for key, value in allowed.items():
            if getattr(schedule, key) != value:
                setattr(schedule, key, value)
                row_changed = True
        if row_changed:
            updated += 1
    db.session.commit()
    audit('schedule.bulk_update', target_type='schedules',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'requested': len(ids), 'updated': updated,
                   'not_found': [i for i in ids if i not in found_ids],
                   'changes': allowed})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'updated': updated,
                    'not_found': [i for i in ids if i not in found_ids]})

@schedules_bp.route('/api/schedules/<int:schedule_id>', methods=['DELETE'])
@api_auth_required(['schedule:write'])
@require_permission('schedule.delete')
def api_delete_schedule(schedule_id):
    schedule = Schedule.query.get_or_404(schedule_id)
    snapshot = {'name': schedule.name, 'playlist_id': schedule.playlist_id,
                'display_id': schedule.display_id, 'group_id': schedule.group_id}
    db.session.delete(schedule)
    db.session.commit()
    audit('schedule.delete', target_type='schedule', target_id=str(schedule_id),
          payload=snapshot)
    return jsonify({
        'status': 'success',
        'message': 'Schedule deleted successfully'
    })


# ---------------------------------------------------------------------------
# Play Now override — temporarily forces a playlist onto display(s)
# Uses a high-priority ephemeral schedule that expires when cancelled.
# ---------------------------------------------------------------------------
@schedules_bp.route('/api/schedules/play-now', methods=['POST'])
@api_auth_required(['schedule:write'])
@require_permission('display.control')
def api_play_now():
    """
    Force-play a playlist on a display or group immediately.
    Pass display_id or group_id + playlist_id.
    Creates a temporary high-priority (999) schedule with no date/time limits.
    Returns the new schedule id so the caller can cancel it later.
    """
    data = request.json or {}
    playlist_id = data.get('playlist_id')
    display_id = data.get('display_id')
    group_id = data.get('group_id')

    if not playlist_id:
        return jsonify({'status': 'error', 'message': 'playlist_id required'}), 400
    if not display_id and not group_id:
        return jsonify({'status': 'error', 'message': 'display_id or group_id required'}), 400

    playlist = Playlist.query.get(playlist_id)
    if not playlist:
        return jsonify({'status': 'error', 'message': 'Playlist not found'}), 404

    # Cancel any existing play-now overrides for the same target
    existing = Schedule.query.filter(
        Schedule.priority == 999,
        Schedule.name.like('__play_now__%')
    )
    if display_id:
        existing = existing.filter(Schedule.display_id == display_id)
    elif group_id:
        existing = existing.filter(Schedule.group_id == group_id)
    for s in existing.all():
        db.session.delete(s)

    sched = Schedule(
        name=f'__play_now__{playlist.name}',
        playlist_id=playlist_id,
        display_id=display_id,
        group_id=group_id,
        priority=999,
        is_active=True,
    )
    db.session.add(sched)
    db.session.commit()

    return jsonify({'status': 'success', 'schedule_id': sched.id,
                    'message': f'Now playing: {playlist.name}'})


@schedules_bp.route('/api/schedules/play-now/<int:schedule_id>/cancel', methods=['POST'])
@api_auth_required(['schedule:write'])
@require_permission('display.control')
def api_play_now_cancel(schedule_id):
    """Cancel an active play-now override."""
    sched = Schedule.query.get_or_404(schedule_id)
    db.session.delete(sched)
    db.session.commit()
    return jsonify({'status': 'success', 'message': 'Play-now override cancelled'})


# ---------------------------------------------------------------------------
# Emergency Broadcast
# ---------------------------------------------------------------------------
@schedules_bp.route('/api/emergency/targets', methods=['GET'])
@login_required
def api_emergency_targets():
    """Displays and groups for the emergency target picker (per tenant)."""
    domain_id, err = _resolve_emergency_domain_id()
    if err:
        return err
    guard = _guard_emergency_use(domain_id)
    if guard:
        return guard
    with bypass_tenant_filter():
        displays = (Display.query.filter_by(domain_id=domain_id)
                    .order_by(Display.name.asc()).all())
        groups = (DisplayGroup.query.filter_by(domain_id=domain_id)
                  .order_by(DisplayGroup.name.asc()).all())
        domain = db.session.get(Domain, domain_id)
    return jsonify({
        'status':    'success',
        'domain_id': domain_id,
        'domain_name': domain.name if domain else None,
        'displays':  [{'id': d.id, 'name': d.name} for d in displays],
        'groups':    [{'id': g.id, 'name': g.name} for g in groups],
    })


@schedules_bp.route('/api/emergency', methods=['GET'])
@login_required
@api_auth_required(['emergency:read'])
def api_emergency_list():
    domain_id, err = _resolve_emergency_domain_id()
    if err:
        return err
    guard = _guard_emergency_use(domain_id)
    if guard:
        return guard
    with bypass_tenant_filter():
        broadcasts = (EmergencyBroadcast.query
                      .filter_by(domain_id=domain_id)
                      .order_by(EmergencyBroadcast.created_at.desc()).all())
    return jsonify({
        'status':     'success',
        'domain_id':  domain_id,
        'broadcasts': [b.to_dict() for b in broadcasts],
    })


@schedules_bp.route('/api/emergency/active', methods=['GET'])
@login_required
def api_emergency_active():
    """Active broadcasts for one tenant (admin UI)."""
    domain_id, err = _resolve_emergency_domain_id()
    if err:
        return err
    guard = _guard_emergency_use(domain_id)
    if guard:
        return guard
    with bypass_tenant_filter():
        rows = (EmergencyBroadcast.query
                .filter_by(domain_id=domain_id, is_active=True)
                .order_by(EmergencyBroadcast.created_at.desc()).all())
    live = [b.to_dict() for b in rows if b.is_live()]
    return jsonify({'status': 'success', 'domain_id': domain_id, 'broadcasts': live})


@schedules_bp.route('/api/emergency', methods=['POST'])
@login_required
@api_auth_required(['emergency:write'])
def api_emergency_create():
    """
    Activate an emergency broadcast.

    Accepts either:
      { "template_id": 3, "target": "all" }           -- fire a saved template (optionally override target)
      { "title": "FIRE", "level": "critical", ... }    -- fully custom broadcast
      { "template_id": 3, "title": "override", ... }   -- template as base, field overrides applied on top
    """
    data = request.get_json(silent=True) or {}
    domain_id, err = _resolve_emergency_domain_id(data)
    if err:
        return err
    guard = _guard_emergency_use(domain_id)
    if guard:
        return guard
    template_id = data.get('template_id')

    # Start from template fields if requested
    base = {}
    if template_id:
        tmpl, err = _get_emergency_template(int(template_id))
        if err:
            return err
        if tmpl.domain_id != domain_id:
            return jsonify({'status': 'error',
                            'message': 'template belongs to another tenant'}), 400
        base = {
            'title': tmpl.title,
            'message': tmpl.message,
            'level': tmpl.level,
            'background_color': tmpl.background_color,
            'text_color': tmpl.text_color,
        }

    # Caller fields override template fields
    title = data.get('title', base.get('title', '')).strip()
    if not title:
        return jsonify({'status': 'error', 'message': 'title required (provide title or template_id)'}), 400

    level = data.get('level', base.get('level', 'critical'))
    if level not in ('info', 'warning', 'critical'):
        level = 'critical'

    default_bg = {'info': '#1565c0', 'warning': '#e65100', 'critical': '#b71c1c'}
    bg_color = data.get('background_color') or base.get('background_color') or default_bg[level]
    text_color = data.get('text_color') or base.get('text_color') or '#ffffff'
    message = data.get('message', base.get('message', ''))
    target = data.get('target', 'all')
    ok, msg = _validate_emergency_target(target, domain_id)
    if not ok:
        return jsonify({'status': 'error', 'message': msg}), 400

    with _emergency_tenant_context(domain_id):
        broadcast = EmergencyBroadcast(
            domain_id=domain_id,
            title=title,
            message=message,
            level=level,
            background_color=bg_color,
            text_color=text_color,
            target=target,
            is_active=True,
            created_by=current_user.id,
        )
        db.session.add(broadcast)
        db.session.commit()

    from display_player import push_emergency
    push_emergency(broadcast)

    src = f'template:{template_id}' if template_id else 'custom'
    logger.info(f"Emergency broadcast ACTIVATED id={broadcast.id} domain={domain_id} "
                f"title='{title}' level={level} source={src} by={current_user.username}")
    audit('emergency.broadcast', target_type='emergency_broadcast',
          target_id=str(broadcast.id),
          payload={'domain_id': domain_id, 'title': title, 'level': level,
                   'target': target, 'source': src})
    return jsonify({'status': 'success', 'broadcast': broadcast.to_dict()})


@schedules_bp.route('/api/emergency/<int:broadcast_id>/cancel', methods=['POST'])
@login_required
@api_auth_required(['emergency:write'])
def api_emergency_cancel(broadcast_id):
    broadcast, err = _get_emergency_broadcast(broadcast_id)
    if err:
        return err
    guard = _guard_emergency_use(broadcast.domain_id)
    if guard:
        return guard
    with _emergency_tenant_context(broadcast.domain_id):
        broadcast.is_active = False
        broadcast.cleared_at = datetime.now()
        broadcast.cleared_by = current_user.id
        db.session.commit()

    from display_player import push_emergency_clear
    push_emergency_clear(broadcast)

    logger.info(f"Emergency broadcast CLEARED id={broadcast_id} domain={broadcast.domain_id} "
                f"by={current_user.username}")
    audit('emergency.cancel', target_type='emergency_broadcast',
          target_id=str(broadcast_id),
          payload={'domain_id': broadcast.domain_id, 'title': broadcast.title})
    return jsonify({'status': 'success', 'message': 'Emergency broadcast cleared'})


@schedules_bp.route('/api/emergency/<int:broadcast_id>', methods=['DELETE'])
@login_required
@api_auth_required(['emergency:write'])
def api_emergency_delete(broadcast_id):
    broadcast, err = _get_emergency_broadcast(broadcast_id)
    if err:
        return err
    if not _can_manage_emergency_templates(broadcast.domain_id):
        return _forbidden_template_manage()
    if broadcast.is_active:
        return jsonify({'status': 'error', 'message': 'Clear the broadcast before deleting it'}), 400
    snapshot = {'domain_id': broadcast.domain_id,
                'title': broadcast.title, 'level': broadcast.level}
    with _emergency_tenant_context(broadcast.domain_id):
        db.session.delete(broadcast)
        db.session.commit()
    audit('emergency.delete', target_type='emergency_broadcast',
          target_id=str(broadcast_id), payload=snapshot)
    return jsonify({'status': 'success', 'message': 'Deleted'})


# ---------------------------------------------------------------------------
# Emergency Templates  (saved presets)
# ---------------------------------------------------------------------------
@schedules_bp.route('/api/emergency/templates', methods=['GET'])
@login_required
@api_auth_required(['emergency:read'])
def api_emergency_templates_list():
    domain_id, err = _resolve_emergency_domain_id()
    if err:
        return err
    guard = _guard_emergency_use(domain_id)
    if guard:
        return guard
    with bypass_tenant_filter():
        templates = (EmergencyTemplate.query
                     .filter_by(domain_id=domain_id)
                     .order_by(EmergencyTemplate.name).all())
    return jsonify({
        'status':     'success',
        'domain_id':  domain_id,
        'templates':  [t.to_dict() for t in templates],
    })


@schedules_bp.route('/api/emergency/templates', methods=['POST'])
@login_required
@api_auth_required(['emergency:write'])
def api_emergency_templates_create():
    data = request.get_json(silent=True) or {}
    domain_id, err = _resolve_emergency_domain_id(data)
    if err:
        return err
    guard = _guard_emergency_use(domain_id)
    if guard:
        return guard
    if not _can_manage_emergency_templates(domain_id):
        return _forbidden_template_manage()
    name = data.get('name', '').strip()
    title = data.get('title', '').strip()
    if not name:
        return jsonify({'status': 'error', 'message': 'name required'}), 400
    if not title:
        return jsonify({'status': 'error', 'message': 'title required'}), 400

    level = data.get('level', 'critical')
    if level not in ('info', 'warning', 'critical'):
        level = 'critical'
    default_bg = {'info': '#1565c0', 'warning': '#e65100', 'critical': '#b71c1c'}

    try:
        with _emergency_tenant_context(domain_id):
            tmpl = EmergencyTemplate(
                domain_id=domain_id,
                name=name,
                title=title,
                message=data.get('message', ''),
                level=level,
                background_color=data.get('background_color') or default_bg[level],
                text_color=data.get('text_color') or '#ffffff',
                created_by=current_user.id,
            )
            db.session.add(tmpl)
            db.session.commit()
    except Exception as e:
        db.session.rollback()
        logger.exception('Emergency template create failed')
        return jsonify({'status': 'error',
                        'message': str(e)}), 500
    logger.info(f"Emergency template CREATED id={tmpl.id} domain={domain_id} "
                f"name='{name}' by={current_user.username}")
    audit('emergency.template_create', target_type='emergency_template',
          target_id=str(tmpl.id),
          payload={'domain_id': domain_id, 'name': name, 'title': title, 'level': level})
    return jsonify({'status': 'success', 'template': _template_to_dict(tmpl)})


@schedules_bp.route('/api/emergency/templates/<int:tmpl_id>', methods=['GET'])
@login_required
@api_auth_required(['emergency:read'])
def api_emergency_templates_get(tmpl_id):
    tmpl, err = _get_emergency_template(tmpl_id)
    if err:
        return err
    return jsonify({'status': 'success', 'template': _template_to_dict(tmpl)})


@schedules_bp.route('/api/emergency/templates/<int:tmpl_id>', methods=['PUT'])
@login_required
@api_auth_required(['emergency:write'])
def api_emergency_templates_update(tmpl_id):
    tmpl, err = _get_emergency_template(tmpl_id)
    if err:
        return err
    if not _can_manage_emergency_templates(tmpl.domain_id):
        return _forbidden_template_manage()
    data = request.get_json(silent=True) or {}
    with _emergency_tenant_context(tmpl.domain_id):
        if 'name' in data:
            tmpl.name = data['name'].strip()
        if 'title' in data:
            tmpl.title = data['title'].strip()
        if 'message' in data:
            tmpl.message = data['message']
        if 'level' in data and data['level'] in ('info', 'warning', 'critical'):
            tmpl.level = data['level']
        if 'background_color' in data:
            tmpl.background_color = data['background_color']
        if 'text_color' in data:
            tmpl.text_color = data['text_color']
        db.session.commit()
    logger.info(f"Emergency template UPDATED id={tmpl_id} by={current_user.username}")
    audit('emergency.template_update', target_type='emergency_template',
          target_id=str(tmpl_id),
          payload={'domain_id': tmpl.domain_id, 'name': tmpl.name})
    return jsonify({'status': 'success', 'template': _template_to_dict(tmpl)})


@schedules_bp.route('/api/emergency/templates/<int:tmpl_id>', methods=['DELETE'])
@login_required
@api_auth_required(['emergency:write'])
def api_emergency_templates_delete(tmpl_id):
    tmpl, err = _get_emergency_template(tmpl_id)
    if err:
        return err
    if not _can_manage_emergency_templates(tmpl.domain_id):
        return _forbidden_template_manage()
    snapshot = {'domain_id': tmpl.domain_id, 'name': tmpl.name, 'title': tmpl.title}
    with _emergency_tenant_context(tmpl.domain_id):
        db.session.delete(tmpl)
        db.session.commit()
    logger.info(f"Emergency template DELETED id={tmpl_id} by={current_user.username}")
    audit('emergency.template_delete', target_type='emergency_template',
          target_id=str(tmpl_id), payload=snapshot)
    return jsonify({'status': 'success', 'message': 'Template deleted'})


@schedules_bp.route('/api/emergency/templates/<int:tmpl_id>/activate', methods=['POST'])
@api_auth_required(['emergency:write'])
def api_emergency_templates_activate(tmpl_id):
    """Convenience: activate a saved template, optional body: { target, title, message }."""
    tmpl, err = _get_emergency_template(tmpl_id)
    if err:
        return err
    if not _can_use_emergency_in_domain(tmpl.domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.json or {}
    domain_id = tmpl.domain_id
    target = data.get('target', 'all')
    ok, msg = _validate_emergency_target(target, domain_id)
    if not ok:
        return jsonify({'status': 'error', 'message': msg}), 400

    level = tmpl.level or 'critical'
    default_bg = {'info': '#1565c0', 'warning': '#e65100', 'critical': '#b71c1c'}

    with _emergency_tenant_context(domain_id):
        broadcast = EmergencyBroadcast(
            domain_id=domain_id,
            title=data.get('title', tmpl.title),
            message=data.get('message', tmpl.message),
            level=level,
            background_color=data.get('background_color') or tmpl.background_color or default_bg[level],
            text_color=data.get('text_color') or tmpl.text_color or '#ffffff',
            target=target,
            is_active=True,
            created_by=current_user.id,
        )
        db.session.add(broadcast)
        db.session.commit()

    from display_player import push_emergency
    push_emergency(broadcast)

    logger.info(f"Emergency broadcast ACTIVATED id={broadcast.id} domain={domain_id} "
                f"title='{broadcast.title}' level={level} source=template:{tmpl_id} "
                f"by={current_user.username}")
    audit('emergency.broadcast', target_type='emergency_broadcast',
          target_id=str(broadcast.id),
          payload={'domain_id': domain_id, 'title': broadcast.title, 'level': level,
                   'target': broadcast.target, 'source': f'template:{tmpl_id}'})
    return jsonify({'status': 'success', 'broadcast': broadcast.to_dict()})


# ---------------------------------------------------------------------------
# Active schedule check helper (used by schedules page for "now playing" badge)
# ---------------------------------------------------------------------------
@schedules_bp.route('/api/schedules/active-now', methods=['GET'])
@api_auth_required(['schedule:read'])
@require_permission('schedule.read')
def api_schedules_active_now():
    """Return the IDs of schedules that are currently active (matching right now)."""
    from datetime import datetime as dt
    now = dt.now()
    today = now.date()
    current_time = now.time()
    active_ids = []
    for sched in Schedule.query.filter_by(is_active=True).all():
        if sched.start_date and sched.start_date > today:
            continue
        if sched.end_date and sched.end_date < today:
            continue
        if sched.days_of_week:
            if str(now.isoweekday()) not in sched.days_of_week.split(','):
                continue
        if sched.start_time and sched.end_time:
            if sched.start_time < sched.end_time:
                if current_time < sched.start_time or current_time > sched.end_time:
                    continue
            else:
                if current_time < sched.start_time and current_time > sched.end_time:
                    continue
        active_ids.append(sched.id)
    return jsonify({'status': 'success', 'active_ids': active_ids})
