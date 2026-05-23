import json
import socket
from urllib.parse import urlparse

from flask import Blueprint, current_app, render_template, request, jsonify, send_from_directory, abort, url_for, Response
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.sql import func
from models import Display, DisplayGroup, DisplayDiagnostic, Domain, Schedule, db
from tenant_filter import bypass_tenant_filter, current_domain_id
from utils import is_online, api_auth_required
from permissions import require_permission
from audit import audit
import os
import uuid

displays_bp = Blueprint('displays', __name__)


def _is_superadmin():
    return getattr(current_user, 'is_superadmin', False)


def _group_syncs_playback(group_id):
    """True when the display group exists and has synchronized playback enabled."""
    if not group_id:
        return False
    g = DisplayGroup.query.get(group_id)
    return bool(g and getattr(g, 'sync_playback', False))


def _display_sync_playback_active(display):
    return (_group_syncs_playback(getattr(display, 'group_id', None))
            and not bool(getattr(display, 'sync_playback_opt_out', False)))


def _is_localhost_name(host):
    host = (host or '').split(':', 1)[0].strip().lower()
    return host in ('', 'localhost', '127.0.0.1', '::1', '0.0.0.0')


def _lan_ip():
    """Best-effort LAN address for setup files when the admin opened localhost."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith('127.'):
            return ip
    except Exception:
        pass
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and not ip.startswith('127.'):
            return ip
    except Exception:
        pass
    return None


def _client_setup_server_url():
    """Base URL for downloaded native client setup files.

    Prefer explicit configuration/DNS. If the admin downloads from localhost,
    replace it with a LAN IP so Android/TV/other devices can reach the server.
    """
    try:
        import settings as _settings
        configured = (_settings.effective_value('server.public_url') or '').strip()
    except Exception:
        configured = ''
    if configured:
        return configured.rstrip('/')

    server_name = (current_app.config.get('SERVER_NAME') or '').strip()
    if server_name:
        scheme = (current_app.config.get('PREFERRED_URL_SCHEME') or request.scheme or 'http').lower()
        return f'{scheme}://{server_name}'.rstrip('/')

    base = (request.url_root or request.host_url or '').rstrip('/')
    parsed = urlparse(base)
    if parsed.scheme and parsed.netloc and not _is_localhost_name(parsed.hostname):
        return base

    ip = _lan_ip()
    if ip and parsed.scheme:
        port = f':{parsed.port}' if parsed.port else ''
        return f'{parsed.scheme}://{ip}{port}'.rstrip('/')

    return base


def _check_single_client(display, client_ip):
    """
    Returns a (403, error response) tuple if another IP is already actively connected
    to this display token, or None if the connection should be allowed.
    An IP is considered 'active' if the display is currently online and the stored
    ip_address differs from the requesting client IP.
    """
    if display.ip_address and display.ip_address != client_ip and is_online(display.last_ping):
        return jsonify({
            'status': 'error',
            'message': 'Another client is already connected with this display token.'
        }), 409
    return None

def _real_client_ip():
    """
    Extract real client IP behind a reverse proxy.

    If ProxyFix is enabled and correctly configured, request.remote_addr is already the client IP.
    This helper also falls back to common proxy headers for environments where ProxyFix isn't active yet.
    Only safe if your app is actually behind your trusted proxy (don’t expose directly to the internet).
    """
    ra = request.remote_addr or ""
    if ra and ra not in ("127.0.0.1", "::1"):
        return ra

    # Prefer well-known original client headers if present (e.g., Cloudflare, Akamai)
    for header in ("CF-Connecting-IP", "True-Client-IP", "X-Real-IP"):
        v = request.headers.get(header)
        if v:
            return v.split(",")[0].strip()

    # Then use access_route (derived from X-Forwarded-For)
    try:
        if request.access_route:
            return request.access_route[0]
    except Exception:
        pass

    # Finally, fall back to X-Forwarded-For first hop
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",")[0].strip()

    return ra or "127.0.0.1"

@displays_bp.route('/displays')
@login_required
@require_permission('display.read')
def displays():
    displays = Display.query.all()
    groups = DisplayGroup.query.all()
    # Augment last_ping with any in-memory heartbeat that hasn't been
    # flushed yet, so the UI doesn't show a display as offline just because
    # the batch interval hasn't elapsed.
    import heartbeat
    for d in displays:
        live = heartbeat.current_state(d.api_key)
        if live and live.get('last_ping') and (not d.last_ping or live['last_ping'] > d.last_ping):
            d.last_ping = live['last_ping']
        d.is_online = is_online(d.last_ping)
    return render_template('displays.html', displays=displays, groups=groups)


@displays_bp.route('/displays/diagnostics')
@login_required
@require_permission('display.read')
def display_diagnostics_page():
    """Central diagnostic log viewer for all displays visible to requester."""
    if _is_superadmin():
        with bypass_tenant_filter():
            displays = Display.query.order_by(Display.name.asc()).all()
            groups = DisplayGroup.query.order_by(DisplayGroup.name.asc()).all()
            domains = Domain.query.order_by(Domain.name.asc()).all()
    else:
        displays = Display.query.order_by(Display.name.asc()).all()
        groups = DisplayGroup.query.order_by(DisplayGroup.name.asc()).all()
        domains = []
    return render_template('display_diagnostics.html',
                           displays=displays,
                           groups=groups,
                           domains=domains,
                           is_superadmin=_is_superadmin())


@displays_bp.route('/display/<int:display_id>')
@login_required
@require_permission('display.read')
def display_detail(display_id):
    display = Display.query.get_or_404(display_id)
    groups = DisplayGroup.query.all()
    import heartbeat
    live = heartbeat.current_state(display.api_key)
    if live and live.get('last_ping') and (not display.last_ping or live['last_ping'] > display.last_ping):
        display.last_ping = live['last_ping']
    display.is_online = is_online(display.last_ping)
    # Last 20 proof-of-play events for this display (best-effort: hidden
    # from the page if the feature is disabled or the table is empty).
    recent_pop = []
    pop_enabled = False
    try:
        import proof_of_play as pop
        pop_enabled = pop.is_enabled()
        recent_pop = pop.query_for_current_domain(display_id=display.id, limit=20)
    except Exception:
        recent_pop = []
    return render_template(
        'display_detail.html',
        display=display,
        groups=groups,
        recent_pop=recent_pop,
        pop_enabled=pop_enabled,
        group_syncs_playback=_display_sync_playback_active(display),
        sync_media_buttons_mismatch=(
            bool(getattr(display, 'show_media_buttons', False))
            and _display_sync_playback_active(display)
        ),
    )

@displays_bp.route('/display/<int:display_id>/edit', methods=['GET', 'POST'])
@login_required
@require_permission('display.assign')
def edit_display(display_id):
    display = Display.query.get_or_404(display_id)
    groups = DisplayGroup.query.all()
    if request.method == 'POST':
        display.name = request.form['name']
        display.location = request.form.get('location', '')
        display.description = request.form.get('description', '')
        display.group_id = request.form.get('group_id', type=int) or None
        display.aspect_mode = request.form.get('aspect_mode')
        display.sync_playback_opt_out = 'sync_playback_opt_out' in request.form
        if _display_sync_playback_active(display):
            display.show_media_buttons = False
        else:
            display.show_media_buttons = 'show_media_buttons' in request.form
        display.show_offline_banner = 'show_offline_banner' in request.form
        display.auto_update_client = 'auto_update_client' in request.form

        # Volume: 0-100, clamp out-of-range silently. Blank = leave unchanged.
        vol_raw = request.form.get('volume')
        if vol_raw not in (None, ''):
            try:
                v = int(vol_raw)
                display.volume = max(0, min(100, v))
            except (TypeError, ValueError):
                pass

        # Unlock PIN: 4-8 digits (numeric only) or empty to disable the lock.
        # Sanitize aggressively so an admin pasting in stray whitespace or
        # non-digits doesn't end up with a PIN they can't actually type on
        # the on-screen keypad.
        pin_raw = (request.form.get('unlock_pin', '') or '').strip()
        pin_digits = ''.join(ch for ch in pin_raw if ch.isdigit())
        if pin_digits == '':
            display.unlock_pin = ''
        elif 4 <= len(pin_digits) <= 8:
            display.unlock_pin = pin_digits
        else:
            from flask import flash
            flash('PIN must be 4-8 digits, or blank to disable the lock.', 'warning')
            return render_template(
                'edit_display.html',
                display=display,
                groups=groups,
                group_syncs_playback=_group_syncs_playback(display.group_id),
            )

        db.session.commit()

        # SSE settings event will fire on the next poll cycle (~5s) so we
        # don't need to push explicitly here -- the player will pick up
        # the new PIN automatically.

        from flask import flash, redirect
        flash('Display updated successfully.', 'success')
        return redirect(url_for('display.display_detail', display_id=display.id))
    return render_template(
        'edit_display.html',
        display=display,
        groups=groups,
        group_syncs_playback=_group_syncs_playback(display.group_id),
    )

# API Endpoints for displays
@displays_bp.route('/api/displays', methods=['GET'])
@api_auth_required(['display:read'])
@require_permission('display.read')
def api_get_displays():
    displays = Display.query.all()
    return jsonify({
        'status': 'success',
        'displays': [d.to_dict() for d in displays]
    })

@displays_bp.route('/api/displays', methods=['POST'])
@api_auth_required(['display:write'])
@require_permission('display.assign')
def api_create_display():
    data = request.json
    if not data or not data.get('name'):
        return jsonify({'status': 'error', 'message': 'Display name is required'}), 400
    device_id = str(uuid.uuid4())
    api_key = str(uuid.uuid4())
    # Auto-generate a 4-digit unlock PIN so new displays are locked-by-default.
    # Admin can change or clear it on the edit-display form.
    import random as _rand
    pin = ''.join(str(_rand.randint(0, 9)) for _ in range(4))
    display = Display(
        name=data.get('name'),
        device_id=device_id,
        api_key=api_key,
        location=data.get('location'),
        group_id=data.get('group_id'),
        unlock_pin=pin,
    )
    db.session.add(display)
    db.session.commit()
    audit('display.create', target_type='display', target_id=str(display.id),
          payload={'name': display.name, 'device_id': device_id})
    return jsonify({'status': 'success', 'message': 'Display created successfully', 'display': display.to_dict()})

@displays_bp.route('/api/displays/<int:display_id>', methods=['GET'])
@api_auth_required(['display:read'])
@require_permission('display.read')
def api_get_display(display_id):
    display = Display.query.get_or_404(display_id)
    return jsonify({'status': 'success', 'display': display.to_dict()})


@displays_bp.route('/api/displays/<int:display_id>/client-config', methods=['GET'])
@login_required
@require_permission('display.read')
def api_display_client_config_download(display_id):
    """JSON bundle for native clients (Android / Electron kiosk import).

    Contains server_url (from this request), display_token (api_key), and
    display_name. Import on device avoids typing long URLs/tokens from USB.
    """
    display = Display.query.get_or_404(display_id)
    base = _client_setup_server_url()
    payload = {
        'format':        'aisignx-player-config',
        'version':       1,
        'server_url':    base,
        'display_token': display.api_key,
        'display_name':  display.name,
    }
    body = json.dumps(payload, indent=2)
    headers = {'Content-Type': 'application/json; charset=utf-8'}
    if request.args.get('download') == '1':
        headers['Content-Disposition'] = (
            f'attachment; filename="aisignx-display-{display.id}.json"'
        )
    return Response(body, headers=headers)

@displays_bp.route('/api/displays/<int:display_id>', methods=['PUT'])
@api_auth_required(['display:write'])
def api_update_display(display_id):
    # display.lockdown gates PIN/input changes; everything else needs display.assign.
    from permissions import has_permission
    from flask_login import current_user
    display = Display.query.get_or_404(display_id)
    data = request.json or {}
    lockdown_keys = {'unlock_pin', 'allow_input', 'show_offline_banner'}
    needs_lockdown = any(k in data for k in lockdown_keys)
    needs_assign = any(k in data for k in ('name', 'location', 'description',
                                            'group_id', 'aspect_mode',
                                            'show_media_buttons', 'volume',
                                            'auto_update_client',
                                            'sync_playback_opt_out',
                                            'diagnostics_enabled'))
    if needs_lockdown and not has_permission(current_user, 'display.lockdown'):
        return jsonify({'status': 'error', 'message': 'forbidden',
                        'permission': 'display.lockdown'}), 403
    if needs_assign and not has_permission(current_user, 'display.assign'):
        return jsonify({'status': 'error', 'message': 'forbidden',
                        'permission': 'display.assign'}), 403

    changes = {}
    if data.get('name'):
        changes['name'] = (display.name, data['name']); display.name = data['name']
    if 'location' in data:
        changes['location'] = (display.location, data['location']); display.location = data['location']
    if 'description' in data:
        changes['description'] = (display.description, data['description']); display.description = data['description']
    if 'group_id' in data:
        changes['group_id'] = (display.group_id, data['group_id']); display.group_id = data['group_id']
    if 'aspect_mode' in data:
        changes['aspect_mode'] = (display.aspect_mode, data['aspect_mode']); display.aspect_mode = data['aspect_mode']
    if 'show_media_buttons' in data:
        display.show_media_buttons = bool(data.get('show_media_buttons'))
    if 'auto_update_client' in data:
        display.auto_update_client = bool(data.get('auto_update_client'))
    if 'sync_playback_opt_out' in data:
        old = bool(getattr(display, 'sync_playback_opt_out', False))
        new = bool(data.get('sync_playback_opt_out'))
        if old != new:
            changes['sync_playback_opt_out'] = (old, new)
        display.sync_playback_opt_out = new
    if 'diagnostics_enabled' in data:
        old = bool(getattr(display, 'diagnostics_enabled', False))
        new = bool(data.get('diagnostics_enabled'))
        if old != new:
            changes['diagnostics_enabled'] = (old, new)
        display.diagnostics_enabled = new
    if 'volume' in data:
        try:
            v = int(data.get('volume'))
            new_vol = max(0, min(100, v))
            changes['volume'] = (display.volume, new_vol)
            display.volume = new_vol
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': 'volume must be an integer 0-100'}), 400
    if 'allow_input' in data:
        display.allow_input = bool(data.get('allow_input'))
    if 'show_offline_banner' in data:
        display.show_offline_banner = bool(data.get('show_offline_banner'))
    if 'unlock_pin' in data:
        # Sanitize: digits only, 4-8 long, or empty to disable lock.
        raw = (data.get('unlock_pin') or '')
        digits = ''.join(ch for ch in str(raw) if ch.isdigit())
        if digits == '' or 4 <= len(digits) <= 8:
            changes['unlock_pin'] = ('***', '***')   # don't log actual PIN
            display.unlock_pin = digits
        else:
            return jsonify({
                'status': 'error',
                'message': 'unlock_pin must be 4-8 digits or blank'
            }), 400
    if _display_sync_playback_active(display):
        display.show_media_buttons = False
    db.session.commit()
    if changes:
        audit('display.update', target_type='display', target_id=str(display.id),
              payload={'changes': {k: {'from': v[0], 'to': v[1]} for k, v in changes.items()}})
    return jsonify({'status': 'success', 'message': 'Display updated successfully', 'display': display.to_dict()})

@displays_bp.route('/api/displays/<int:display_id>', methods=['DELETE'])
@api_auth_required(['display:write'])
@require_permission('display.assign')
def api_delete_display(display_id):
    display = Display.query.get_or_404(display_id)
    snapshot = {'name': display.name, 'device_id': display.device_id,
                'group_id': display.group_id}
    db.session.delete(display)
    db.session.commit()
    audit('display.delete', target_type='display', target_id=str(display_id),
          payload=snapshot)
    return jsonify({'status': 'success', 'message': 'Display deleted successfully'})


@displays_bp.route('/api/displays/bulk-delete', methods=['POST'])
@api_auth_required(['display:write'])
@require_permission('display.assign')
def api_bulk_delete_displays():
    """Delete many displays visible to the requester.

    Tenant filtering is applied by Display.query, so superusers can delete
    across all tenants while regular tenant admins can only delete displays in
    their active tenant.
    """
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400

    rows = Display.query.filter(Display.id.in_(ids)).all()
    found_ids = {d.id for d in rows}
    snapshots = []
    results = []
    for display in rows:
        snapshots.append({'id': display.id, 'name': display.name,
                          'device_id': display.device_id,
                          'group_id': display.group_id,
                          'domain_id': display.domain_id})
        # Keep schedules but detach them from the removed display. This mirrors
        # the nullable Schedule.display_id model and avoids orphaned references
        # on databases that do not enforce FK cascades.
        Schedule.query.filter_by(display_id=display.id).update(
            {'display_id': None}, synchronize_session=False)
        DisplayDiagnostic.query.filter_by(display_id=display.id).delete(
            synchronize_session=False)
        try:
            display.schedules.delete(synchronize_session=False)
        except Exception:
            pass
        db.session.delete(display)
        results.append({'id': display.id, 'ok': True})
    db.session.commit()

    not_found = [i for i in ids if i not in found_ids]
    audit('display.bulk_delete', target_type='displays',
          target_id=','.join(str(s['id']) for s in snapshots),
          payload={'requested': len(ids), 'deleted': len(snapshots),
                   'not_found': not_found, 'displays': snapshots,
                   'results': results})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'deleted': len(snapshots), 'not_found': not_found,
                    'results': results})


@displays_bp.route('/api/displays/<int:display_id>/command', methods=['POST'])
@api_auth_required(['display:write'])
@require_permission('display.control')
def api_send_display_command(display_id):
    """Push a one-off command (reboot, update, etc.) to a connected display.
    The display receives it instantly via SSE and acts on it (Electron app only).
    """
    display = Display.query.get_or_404(display_id)
    data = request.json or {}
    action = (data.get('action') or '').strip().lower()
    valid = {'reboot', 'update', 'reload'}
    if action not in valid:
        return jsonify({'status': 'error',
                        'message': f'action must be one of: {", ".join(sorted(valid))}'}), 400
    # Lazy import to avoid circular dep
    from display_player import push_command
    delivered = push_command(display.api_key, action, data.get('payload'))
    audit('display.command', target_type='display', target_id=str(display.id),
          payload={'action': action, 'delivered': delivered})
    return jsonify({
        'status': 'success',
        'delivered': delivered,
        'message': ('Command queued and delivered.' if delivered
                    else 'Display is not currently connected; the command was discarded.')
    })


@displays_bp.route('/api/displays/<int:display_id>/snooze-alerts', methods=['POST'])
@require_permission('display.control')
def api_display_snooze_alerts(display_id):
    """Suppress offline alerts for one display for `hours` hours.
    Pass {"hours": 0} to clear an existing snooze. Useful when a display
    is intentionally offline (maintenance, store closed, etc.) so on-call
    isn't paged for the planned outage."""
    display = Display.query.get_or_404(display_id)
    data = request.json or {}
    try:
        hours  = float(data.get('hours', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'hours must be numeric'}), 400
    reason = (data.get('reason') or '').strip()[:200]
    try:
        import alerts as _alerts
        result = _alerts.snooze_display(display.id, hours, reason=reason)
    except Exception as exc:
        return jsonify({'status': 'error', 'message': str(exc)}), 500
    audit('display.alert_snoozed', target_type='display',
          target_id=str(display.id),
          payload={'hours': hours, 'reason': reason,
                   'snoozed_until': result.get('snoozed_until')})
    return jsonify({'status': 'success', **result})


@displays_bp.route('/api/displays/<int:display_id>/rotate-key', methods=['POST'])
@require_permission('display.control')
def api_display_rotate_key(display_id):
    """Rotate the API key for a display. The old key is invalidated
    immediately; the player must be re-provisioned with the new one
    (token URL or pairing). Audited; the new key is returned ONCE in
    the response body and never logged."""
    display = Display.query.get_or_404(display_id)
    old_prefix = (display.api_key or '')[:8]
    new_key = str(uuid.uuid4())
    display.api_key = new_key
    db.session.commit()
    # Drop any in-memory SSE/heartbeat state keyed by the old api_key
    # so the next valid connection comes from the rotated identity.
    try:
        from display_player import push_command   # noqa: F401  (lazy import)
        import display_player as _dp
        if hasattr(_dp, '_sse_streams'):
            _dp._sse_streams.pop(old_prefix, None)
    except Exception:
        pass
    try:
        import heartbeat as _hb
        if hasattr(_hb, 'forget'):
            _hb.forget(old_prefix)
    except Exception:
        pass
    audit('display.api_key_rotated', target_type='display',
          target_id=str(display.id),
          payload={'old_key_prefix': old_prefix,
                   'new_key_prefix': new_key[:8]})
    return jsonify({
        'status':  'success',
        'message': 'API key rotated. The old key has been invalidated.',
        'api_key': new_key,
    })


@displays_bp.route('/api/displays/<int:display_id>/diagnostics', methods=['GET'])
@login_required
@require_permission('display.read')
def api_display_diagnostics_list(display_id):
    """Recent diagnostic log entries for one display.

    Query string:
      ?limit=200       (default 200, max 1000)
      ?level=error,sync,net  (optional filter)
      ?since=<id>      (return entries with id > since; for live tail)
    """
    display = Display.query.get_or_404(display_id)
    try:
        limit = int(request.args.get('limit') or 200)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(1000, limit))
    levels_raw = (request.args.get('level') or '').strip().lower()
    levels = [s for s in (x.strip() for x in levels_raw.split(',')) if s]
    try:
        since = int(request.args.get('since') or 0)
    except (TypeError, ValueError):
        since = 0
    q = DisplayDiagnostic.query.filter_by(display_id=display.id)
    if since > 0:
        q = q.filter(DisplayDiagnostic.id > since)
    if levels:
        q = q.filter(DisplayDiagnostic.level.in_(levels))
    rows = q.order_by(DisplayDiagnostic.id.desc()).limit(limit).all()
    rows.reverse()
    return jsonify({
        'status':              'success',
        'enabled':             bool(getattr(display, 'diagnostics_enabled', False)),
        'entries':             [r.to_dict() for r in rows],
        'last_id':             rows[-1].id if rows else since,
    })


@displays_bp.route('/api/displays/diagnostics', methods=['GET'])
@login_required
@require_permission('display.read')
def api_all_display_diagnostics_list():
    """Central diagnostics list. Superusers can query all tenants or filter by
    tenant; regular users/admins are limited to their active tenant."""
    try:
        limit = int(request.args.get('limit') or 300)
    except (TypeError, ValueError):
        limit = 300
    limit = max(1, min(2000, limit))
    try:
        since = int(request.args.get('since') or 0)
    except (TypeError, ValueError):
        since = 0
    display_id = request.args.get('display_id', type=int)
    group_raw = (request.args.get('group_id') or '').strip().lower()
    group_id = request.args.get('group_id', type=int)
    domain_id = request.args.get('domain_id', type=int)
    levels_raw = (request.args.get('level') or '').strip().lower()
    levels = [s for s in (x.strip() for x in levels_raw.split(',')) if s]
    search = (request.args.get('q') or '').strip()

    def _query():
        q = (DisplayDiagnostic.query
             .join(Display, DisplayDiagnostic.display_id == Display.id))
        if not _is_superadmin():
            q = q.filter(Display.domain_id == current_domain_id())
        elif domain_id:
            q = q.filter(Display.domain_id == domain_id)
        if display_id:
            q = q.filter(DisplayDiagnostic.display_id == display_id)
        if group_raw in ('none', '__none__'):
            q = q.filter(Display.group_id == None)
        elif group_id:
            q = q.filter(Display.group_id == group_id)
        if since > 0:
            q = q.filter(DisplayDiagnostic.id > since)
        if levels:
            q = q.filter(DisplayDiagnostic.level.in_(levels))
        if search:
            like = f'%{search}%'
            q = q.filter(or_(DisplayDiagnostic.message.like(like),
                             DisplayDiagnostic.source.like(like),
                             Display.name.like(like)))
        return q.order_by(DisplayDiagnostic.id.desc()).limit(limit).all()

    if _is_superadmin():
        with bypass_tenant_filter():
            rows = _query()
            display_ids = {r.display_id for r in rows}
            displays = {d.id: d for d in Display.query.filter(Display.id.in_(display_ids)).all()} if display_ids else {}
            domain_ids = {d.domain_id for d in displays.values() if d.domain_id}
            domains = {d.id: d for d in Domain.query.filter(Domain.id.in_(domain_ids)).all()} if domain_ids else {}
    else:
        rows = _query()
        display_ids = {r.display_id for r in rows}
        displays = {d.id: d for d in Display.query.filter(Display.id.in_(display_ids)).all()} if display_ids else {}
        domains = {}
    rows.reverse()

    entries = []
    for r in rows:
        item = r.to_dict()
        d = displays.get(r.display_id)
        item['display_name'] = d.name if d else None
        item['domain_id'] = d.domain_id if d else None
        item['domain_name'] = (domains.get(d.domain_id).name
                               if d and d.domain_id in domains else None)
        entries.append(item)
    return jsonify({'status': 'success',
                    'entries': entries,
                    'last_id': rows[-1].id if rows else since,
                    'scope': 'all' if _is_superadmin() and not domain_id else 'tenant'})


@displays_bp.route('/api/displays/<int:display_id>/diagnostics', methods=['DELETE'])
@login_required
@require_permission('display.assign')
def api_display_diagnostics_clear(display_id):
    """Delete all diagnostic log entries for this display."""
    display = Display.query.get_or_404(display_id)
    deleted = db.session.query(DisplayDiagnostic).filter_by(
        display_id=display.id).delete(synchronize_session=False)
    db.session.commit()
    audit('display.diagnostics_cleared', target_type='display',
          target_id=str(display.id), payload={'deleted': int(deleted)})
    return jsonify({'status': 'success', 'deleted': int(deleted)})


@displays_bp.route('/api/displays/bulk-update', methods=['POST'])
@api_auth_required(['display:write'])
def api_bulk_update_displays():
    """Apply the same field changes to a list of displays in one call.

    Body: {"ids": [int,...], "changes": {<field>: <value>, ...}}

    Permitted change fields (in this bulk endpoint):
       group_id, location, aspect_mode, auto_update_client, sync_playback_opt_out,
       allow_input, show_offline_banner, show_media_buttons,
       volume, unlock_pin

    Per-tenant filtering is automatic via Display.query (only displays the
    caller can already see are eligible). Unknown fields are silently
    ignored. Per-row guard ensures we never set show_media_buttons=True
    for a display whose group syncs playback.
    """
    from permissions import has_permission
    from flask_login import current_user

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

    lockdown_keys = {'unlock_pin', 'allow_input', 'show_offline_banner'}
    assign_keys = {'name', 'location', 'description', 'group_id', 'aspect_mode',
                   'show_media_buttons', 'volume', 'auto_update_client',
                   'sync_playback_opt_out',
                   'diagnostics_enabled'}
    needs_lockdown = any(k in changes for k in lockdown_keys)
    needs_assign = any(k in changes for k in assign_keys)
    if needs_lockdown and not has_permission(current_user, 'display.lockdown'):
        return jsonify({'status': 'error', 'message': 'forbidden',
                        'permission': 'display.lockdown'}), 403
    if needs_assign and not has_permission(current_user, 'display.assign'):
        return jsonify({'status': 'error', 'message': 'forbidden',
                        'permission': 'display.assign'}), 403

    allowed_fields = lockdown_keys | assign_keys - {'name'}  # name excluded from bulk
    sanitized = {}
    for k, v in changes.items():
        if k not in allowed_fields:
            continue
        if k == 'group_id':
            if v in ('', None):
                sanitized['group_id'] = None
            else:
                try: sanitized['group_id'] = int(v)
                except (TypeError, ValueError):
                    return jsonify({'status':'error','message':'group_id must be int or null'}), 400
        elif k == 'volume':
            try:
                sanitized['volume'] = max(0, min(100, int(v)))
            except (TypeError, ValueError):
                return jsonify({'status':'error','message':'volume must be 0-100'}), 400
        elif k == 'unlock_pin':
            digits = ''.join(ch for ch in str(v or '') if ch.isdigit())
            if digits and not (4 <= len(digits) <= 8):
                return jsonify({'status':'error','message':'unlock_pin must be 4-8 digits or blank'}), 400
            sanitized['unlock_pin'] = digits
        elif k in ('auto_update_client', 'allow_input', 'show_offline_banner',
                   'show_media_buttons', 'diagnostics_enabled',
                   'sync_playback_opt_out'):
            sanitized[k] = bool(v)
        else:
            sanitized[k] = v
    if not sanitized:
        return jsonify({'status': 'error', 'message': 'no recognized fields in changes'}), 400

    rows = Display.query.filter(Display.id.in_(ids)).all()
    found_ids = {d.id for d in rows}
    updated = 0
    for d in rows:
        for k, v in sanitized.items():
            setattr(d, k, v)
        if _display_sync_playback_active(d):
            d.show_media_buttons = False
        updated += 1
    db.session.commit()

    not_found = [i for i in ids if i not in found_ids]
    audit('displays.bulk_update', target_type='displays',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'changes': {k: ('***' if k == 'unlock_pin' else v)
                               for k, v in sanitized.items()},
                   'requested': len(ids), 'updated': updated,
                   'not_found': not_found})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'updated': updated, 'not_found': not_found})


@displays_bp.route('/api/displays/command', methods=['POST'])
@api_auth_required(['display:write'])
@require_permission('display.control')
def api_bulk_display_command():
    """Push the same command to a list of display IDs in one call.

    Body: {"action": "reload"|"reboot"|"update",
           "ids":    [int, ...],
           "payload": {...optional}}

    Tenant-scoped: only displays the caller can already see (via the
    standard tenant filter on Display.query) are eligible. Unknown ids
    are reported back as `not_found` rather than failing the whole call,
    so partial-fleet selections still work.
    """
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip().lower()
    valid = {'reboot', 'update', 'reload'}
    if action not in valid:
        return jsonify({'status': 'error',
                        'message': f'action must be one of: {", ".join(sorted(valid))}'}), 400
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400

    rows = Display.query.filter(Display.id.in_(ids)).all()
    found_ids = {d.id for d in rows}

    from display_player import push_command
    payload = data.get('payload')
    delivered_count = 0
    results = []
    for d in rows:
        ok = push_command(d.api_key, action, payload)
        if ok:
            delivered_count += 1
        results.append({'id': d.id, 'name': d.name, 'delivered': ok})

    not_found = [i for i in ids if i not in found_ids]
    audit('displays.bulk_command', target_type='displays',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'action': action,
                   'requested': len(ids),
                   'targets':   len(rows),
                   'delivered': delivered_count,
                   'not_found': not_found})

    return jsonify({
        'status':    'success',
        'action':    action,
        'requested': len(ids),
        'targets':   len(rows),
        'delivered': delivered_count,
        'results':   results,
        'not_found': not_found,
    })


@displays_bp.route('/api/displays/issues', methods=['GET'])
@login_required
@require_permission('display.read')
def api_display_issues():
    """Lightweight per-display health summary the displays page polls every
    ~20 s. Returns one row per display with an `issues` list, so the UI can
    show a badge column without a full page reload.

    Surfaced issues:
      * `offline`         -- last ping older than the online window
      * `version_drift`   -- display in a group whose members report a
                             mix of `app_version` values (helps spot
                             half-rolled-out updates)
      * `no_version`      -- online display has never reported a version
    """
    import heartbeat
    rows = Display.query.all()

    # Snapshot of which displays the alerts module currently has flagged
    # as in an active outage. Read-only access to the in-memory dict; if
    # the alerts module isn't loaded yet (very first request after boot)
    # we just treat it as empty.
    try:
        import alerts as _alerts
        _alerted_ids = set(_alerts._alert_state.keys())
        _snoozed_until = dict(_alerts.snoozes())
    except Exception:
        _alerted_ids = set()
        _snoozed_until = {}

    # Build per-group app_version sets for drift detection. Use getattr
    # so a not-yet-migrated install (no `display.app_version` column)
    # still returns 200 instead of 500 -- the schema evolver will add
    # the column on the next boot.
    by_group = {}
    for d in rows:
        gid = d.group_id
        if gid is None:
            continue
        ver = getattr(d, 'app_version', None) or ''
        by_group.setdefault(gid, set()).add(ver)
    drift_groups = {gid for gid, vs in by_group.items()
                    if len(vs - {''}) > 1}

    out = []
    for d in rows:
        # Same in-memory heartbeat fold-in as the displays page render.
        live = heartbeat.current_state(d.api_key)
        last_ping = d.last_ping
        if live and live.get('last_ping') and (
                not last_ping or live['last_ping'] > last_ping):
            last_ping = live['last_ping']
        online = is_online(last_ping)

        ver = getattr(d, 'app_version', None)
        issues = []
        if not online:
            issues.append('offline')
        else:
            if not ver:
                issues.append('no_version')
            if d.group_id in drift_groups and ver:
                issues.append('version_drift')

        if d.id in _alerted_ids:
            issues.append('alert_active')
        if d.id in _snoozed_until:
            issues.append('alert_snoozed')

        out.append({
            'id':          d.id,
            'name':        d.name,
            'online':      online,
            'app_version': ver or None,
            'group_id':    d.group_id,
            'last_ping':   last_ping.isoformat() if last_ping else None,
            'issues':      issues,
            'alert_active': d.id in _alerted_ids,
            'alert_snoozed_until': _snoozed_until.get(d.id),
        })

    return jsonify({
        'status':   'success',
        'displays': out,
        'summary':  {
            'total':     len(out),
            'online':    sum(1 for r in out if r['online']),
            'with_issues': sum(1 for r in out if r['issues']),
            'alerts_active': len(_alerted_ids),
            'alerts_snoozed': len(_snoozed_until),
        },
    })


@displays_bp.route('/api/displays/by_apikey', methods=['GET'])
def api_display_by_apikey():
    api_key = request.args.get('api_key', '')
    display = Display.query.filter_by(api_key=api_key).first()
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid API key'}), 404
    return jsonify({'status': 'success', 'display': display.to_dict()})

@displays_bp.route('/api/display/debug', methods=['GET'])
def api_display_debug():
    """Debug endpoint to verify display authentication and show server-observed IP."""
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.lower().startswith("bearer "):
        return jsonify({'status': 'error', 'message': 'Missing or invalid API key format - should be "Bearer YOUR_API_KEY"'}), 401
    api_key = auth_header.split(" ", 1)[1].strip()
    display = Display.query.filter_by(api_key=api_key).first()
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid API key - no display found'}), 404

    return jsonify({
        'status': 'success',
        'message': 'Display authenticated successfully',
        'client_ip': _real_client_ip(),
        'remote_addr': request.remote_addr,
        'access_route': list(request.access_route or []),
        'xff': request.headers.get('X-Forwarded-For'),
        'display': {
            'id': display.id,
            'name': display.name,
            'device_id': display.device_id,
            'group_id': display.group_id
        }
    })

@displays_bp.route('/api/display/ping', methods=['POST'])
def api_display_ping_by_key():
    api_key = request.headers.get('Authorization', '').replace('Bearer ', '')
    display = Display.query.filter_by(api_key=api_key).first()
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid API key'}), 404

    client_ip = _real_client_ip()
    conflict = _check_single_client(display, client_ip)
    if conflict:
        return conflict

    display.last_ping = func.now()
    display.ip_address = client_ip
    db.session.commit()
    return jsonify({'status': 'success'})