"""
Admin / user management blueprint.

User accounts are GLOBAL (not tenant-scoped). Their per-tenant access is
controlled by UserDomainRole rows. The endpoints here manage account-level
attributes (username/email/password/active/is_superadmin); per-tenant role
assignment lives elsewhere (Phase 2 admin UI; CLI for now).

Authorization model
-------------------
Only superadmins can:
    * list all users in the system
    * create, update or delete other users
    * toggle is_superadmin
A user can always read and update their own account (username, email,
password) -- but cannot change is_superadmin or active on themselves.
"""
from flask import Blueprint, render_template, request, redirect, url_for, flash, jsonify
from flask_login import current_user, login_required
import secrets

from models import User, UserDomainRole, Role, Domain, Display, db
from tenant_filter import bypass_tenant_filter
from utils import is_valid_password
from audit import audit
from permissions import require_permission
from user_accounts import username_taken, email_taken


admin_bp = Blueprint('admin', __name__)


def _is_superadmin():
    return getattr(current_user, 'is_superadmin', False)


@admin_bp.route('/users')
@login_required
def users():
    """User management — superadmin (all tenants) or domain admin (own tenants)."""
    if not _can_access_users_admin():
        flash('Unauthorized. Tenant administrator access required.', 'danger')
        return redirect(url_for('main.dashboard'))
    all_users = _users_for_admin_list()
    user_domains = _role_rows_for_users([u.id for u in all_users])
    with bypass_tenant_filter():
        if _is_superadmin():
            domains = Domain.query.order_by(Domain.name.asc()).all()
        else:
            admin_ids = _administrable_domain_ids()
            domains = (Domain.query.filter(Domain.id.in_(admin_ids))
                       .order_by(Domain.name.asc()).all()) if admin_ids else []
    from tokens import ALL_SCOPES
    return render_template('users.html', users=all_users, domains=domains,
                           user_domains=user_domains, all_scopes=ALL_SCOPES,
                           is_superadmin=_is_superadmin(),
                           can_manage_all_tenants=_is_superadmin())


@admin_bp.route('/api/users', methods=['GET'])
@login_required
def api_get_users():
    if not _can_access_users_admin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    return jsonify({
        'status': 'success',
        'users': [u.to_dict() for u in _users_for_admin_list()],
    })


@admin_bp.route('/api/users', methods=['POST'])
@login_required
def api_create_user():
    if not _can_access_users_admin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.json or {}
    is_service = bool(data.get('is_service_account', False))
    is_super = bool(data.get('is_superadmin', False)) and not is_service
    if is_super and not _is_superadmin():
        return jsonify({'status': 'error',
                        'message': 'Only superadmins can create superadmin accounts'}), 403
    if not data.get('username') or not data.get('email'):
        return jsonify({'status': 'error', 'message': 'Username and email are required'}), 400
    password = data.get('password') or ''
    if is_service:
        password = password or secrets.token_urlsafe(32)
    elif not password:
        return jsonify({'status': 'error', 'message': 'Password is required for interactive users'}), 400

    home_domain_id = None
    if not is_super:
        try:
            home_domain_id = int(data.get('home_domain_id') or data.get('domain_id'))
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': 'home_domain_id (tenant) is required for tenant users'}), 400
        if not _can_admin_domain(home_domain_id):
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403
        with bypass_tenant_filter():
            if db.session.get(Domain, home_domain_id) is None:
                return jsonify({'status': 'error', 'message': 'tenant not found'}), 404

    if username_taken(home_domain_id, data['username']):
        return jsonify({'status': 'error',
                        'message': 'Username already exists in this tenant'}), 400
    if email_taken(home_domain_id, data['email']):
        return jsonify({'status': 'error',
                        'message': 'Email already exists in this tenant'}), 400
    if not is_service and not is_valid_password(password):
        return jsonify({'status': 'error', 'message': 'Password does not meet security requirements'}), 400
    user = User(
        username=data['username'],
        email=data['email'],
        home_domain_id=home_domain_id,
        is_superadmin=is_super,
        is_service_account=is_service,
        active=True,
    )
    user.set_password(password)
    with bypass_tenant_filter():
        db.session.add(user)
        db.session.commit()
    audit('user.create', target_type='user', target_id=str(user.id),
          payload={'username': user.username, 'email': user.email,
                   'home_domain_id': user.home_domain_id,
                   'is_superadmin': user.is_superadmin,
                   'is_service_account': user.is_service_account},
          domain_id=home_domain_id)
    return jsonify({
        'status': 'success',
        'message': 'User created successfully',
        'user': user.to_dict(),
    })


@admin_bp.route('/api/users/<int:user_id>', methods=['GET'])
@login_required
def api_get_user(user_id):
    if current_user.id != user_id and not _can_manage_user_id(user_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():
        user = User.query.get_or_404(user_id)
    return jsonify({'status': 'success', 'user': user.to_dict()})


@admin_bp.route('/api/users/<int:user_id>', methods=['PUT'])
@login_required
def api_update_user(user_id):
    if current_user.id != user_id and not _can_manage_user_id(user_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():
        user = User.query.get_or_404(user_id)
    data = request.json or {}
    changes = {}

    # Privileged-only fields. Reject if a non-superadmin tries to set them.
    for priv_field in ('is_superadmin', 'is_service_account'):
        if priv_field in data and bool(data[priv_field]) != bool(getattr(user, priv_field)):
            if not _is_superadmin():
                return jsonify({'status': 'error',
                                'message': f'Only superadmins can change {priv_field}'}), 403
            # Block superadmin from disabling/demoting themselves to avoid lockout.
            if user.id == current_user.id:
                return jsonify({'status': 'error',
                                'message': f'Cannot change {priv_field} on your own account'}), 400

    home_did = user.home_domain_id

    if data.get('username') and data['username'] != user.username:
        if username_taken(home_did, data['username'], exclude_user_id=user.id):
            return jsonify({'status': 'error',
                            'message': 'Username already exists in this tenant'}), 400
        changes['username'] = (user.username, data['username'])
        user.username = data['username']

    if data.get('email') and data['email'] != user.email:
        if email_taken(home_did, data['email'], exclude_user_id=user.id):
            return jsonify({'status': 'error',
                            'message': 'Email already exists in this tenant'}), 400
        changes['email'] = (user.email, data['email'])
        user.email = data['email']

    if getattr(user, 'is_service_account', False) and data.get('password'):
        return jsonify({'status': 'error',
                        'message': 'Service accounts cannot use web passwords; use API tokens'}), 400

    if data.get('password'):
        if not is_valid_password(data['password']):
            return jsonify({'status': 'error', 'message': 'Password does not meet security requirements'}), 400
        user.set_password(data['password'])
        changes['password'] = ('***', '***')

    if _is_superadmin():
        if 'is_superadmin' in data:
            new_v = bool(data['is_superadmin'])
            if new_v != user.is_superadmin:
                changes['is_superadmin'] = (user.is_superadmin, new_v)
            user.is_superadmin = new_v
        if 'active' in data:
            new_v = bool(data['active'])
            if new_v != user.active:
                changes['active'] = (user.active, new_v)
            user.active = new_v
        if 'is_service_account' in data:
            new_v = bool(data['is_service_account'])
            if new_v != user.is_service_account:
                if user.is_superadmin and new_v:
                    return jsonify({'status': 'error',
                                    'message': 'Superadmins cannot be service accounts'}), 400
                changes['is_service_account'] = (user.is_service_account, new_v)
            user.is_service_account = new_v
    elif _can_manage_user_id(user_id) and 'active' in data:
        new_v = bool(data['active'])
        if new_v != user.active:
            changes['active'] = (user.active, new_v)
        user.active = new_v

    with bypass_tenant_filter():
        db.session.commit()

    if changes:
        audit('user.update', target_type='user', target_id=str(user.id),
              payload={'changes': {k: {'from': v[0], 'to': v[1]}
                                   for k, v in changes.items()}})
    return jsonify({'status': 'success', 'message': 'User updated successfully',
                    'user': user.to_dict()})


@admin_bp.route('/api/users/<int:user_id>', methods=['DELETE'])
@login_required
def api_delete_user(user_id):
    if not _can_manage_user_id(user_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    if current_user.id == user_id:
        return jsonify({'status': 'error', 'message': 'You cannot delete your own account'}), 400
    with bypass_tenant_filter():
        user = User.query.get_or_404(user_id)
        snapshot = {'username': user.username, 'email': user.email,
                    'is_superadmin': user.is_superadmin}
        # UserDomainRole rows cascade via FK ondelete=CASCADE.
        db.session.delete(user)
        db.session.commit()
    audit('user.delete', target_type='user', target_id=str(user_id), payload=snapshot)
    return jsonify({'status': 'success', 'message': 'User deleted successfully'})


@admin_bp.route('/api/users/bulk-update', methods=['POST'])
@login_required
def api_bulk_update_users():
    """Bulk edit safe account-level user fields.

    Body: {"ids": [int,...], "changes": {"active": bool,
                                         "is_superadmin": bool}}
    Username, email and passwords are intentionally excluded from bulk edit.
    The current logged-in account is skipped to prevent accidental lockout.
    """
    if not _can_access_users_admin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    changes = data.get('changes') or {}
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400
    allowed = {}
    for field in ('active', 'is_superadmin'):
        if field in changes:
            allowed[field] = bool(changes[field])
    if 'is_superadmin' in allowed and not _is_superadmin():
        return jsonify({'status': 'error',
                        'message': 'Only superadmins can change superadmin status'}), 403
    if not allowed:
        return jsonify({'status': 'error',
                        'message': 'no recognized fields in changes'}), 400

    with bypass_tenant_filter():
        rows = User.query.filter(User.id.in_(ids)).all()
        found_ids = {u.id for u in rows}
        updated = 0
        results = []
        for u in rows:
            if not _can_manage_user_id(u.id):
                results.append({'id': u.id, 'ok': False, 'error': 'forbidden'})
                continue
            if u.id == current_user.id:
                results.append({'id': u.id, 'ok': False,
                                'error': 'cannot bulk edit your own account'})
                continue
            row_changes = {}
            for k, v in allowed.items():
                if bool(getattr(u, k)) != v:
                    row_changes[k] = {'from': bool(getattr(u, k)), 'to': v}
                    setattr(u, k, v)
            if row_changes:
                updated += 1
            results.append({'id': u.id, 'ok': True, 'changes': row_changes})
        db.session.commit()

    not_found = [i for i in ids if i not in found_ids]
    audit('users.bulk_update', target_type='users',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'requested': len(ids), 'updated': updated,
                   'not_found': not_found, 'changes': allowed,
                   'results': results})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'updated': updated, 'not_found': not_found,
                    'results': results})


@admin_bp.route('/api/users/bulk-delete', methods=['POST'])
@login_required
def api_bulk_delete_users():
    """Delete many users. The current user is always skipped."""
    if not _can_access_users_admin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400
    with bypass_tenant_filter():
        rows = User.query.filter(User.id.in_(ids)).all()
        found_ids = {u.id for u in rows}
        deleted = 0
        results = []
        snapshots = []
        for u in rows:
            if not _can_manage_user_id(u.id):
                results.append({'id': u.id, 'ok': False, 'error': 'forbidden'})
                continue
            if u.id == current_user.id:
                results.append({'id': u.id, 'ok': False,
                                'error': 'cannot delete your own account'})
                continue
            snapshots.append({'id': u.id, 'username': u.username,
                              'email': u.email,
                              'is_superadmin': bool(u.is_superadmin)})
            db.session.delete(u)
            deleted += 1
            results.append({'id': u.id, 'ok': True})
        db.session.commit()
    not_found = [i for i in ids if i not in found_ids]
    audit('users.bulk_delete', target_type='users',
          target_id=','.join(str(i['id']) for i in snapshots),
          payload={'requested': len(ids), 'deleted': deleted,
                   'not_found': not_found, 'users': snapshots,
                   'results': results})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'deleted': deleted, 'not_found': not_found,
                    'results': results})


@admin_bp.route('/api/users/bulk-roles', methods=['POST'])
@login_required
def api_bulk_user_roles():
    """Bulk add/remove one role assignment for many users in one tenant."""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    action = (data.get('action') or '').strip().lower()
    try:
        domain_id = int(data.get('domain_id'))
        role_id = int(data.get('role_id'))
    except (TypeError, ValueError):
        return jsonify({'status': 'error',
                        'message': 'domain_id and role_id are required integers'}), 400
    if action not in ('add', 'remove'):
        return jsonify({'status': 'error',
                        'message': 'action must be add or remove'}), 400
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400
    if not _can_admin_domain(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    with bypass_tenant_filter():
        role = db.session.get(Role, role_id)
        domain = db.session.get(Domain, domain_id)
        if domain is None:
            return jsonify({'status': 'error', 'message': 'tenant not found'}), 404
        if role is None:
            return jsonify({'status': 'error', 'message': 'role not found'}), 404
        if role.domain_id is not None and role.domain_id != domain_id:
            return jsonify({'status': 'error',
                            'message': 'role is not assignable in this tenant'}), 400

        rows = User.query.filter(User.id.in_(ids)).all()
        found_ids = {u.id for u in rows}
        changed = 0
        results = []
        for user in rows:
            if action == 'add':
                existing = UserDomainRole.query.filter_by(
                    user_id=user.id, domain_id=domain_id, role_id=role_id).first()
                if existing:
                    results.append({'id': user.id, 'ok': True,
                                    'changed': False, 'reason': 'already assigned'})
                    continue
                db.session.add(UserDomainRole(user_id=user.id,
                                              domain_id=domain_id,
                                              role_id=role_id))
                changed += 1
                results.append({'id': user.id, 'ok': True, 'changed': True})
            else:
                assignments = UserDomainRole.query.filter_by(
                    user_id=user.id, domain_id=domain_id, role_id=role_id).all()
                if not assignments:
                    results.append({'id': user.id, 'ok': True,
                                    'changed': False, 'reason': 'not assigned'})
                    continue
                for assignment in assignments:
                    db.session.delete(assignment)
                changed += len(assignments)
                results.append({'id': user.id, 'ok': True,
                                'changed': True, 'removed': len(assignments)})
        db.session.commit()

    not_found = [i for i in ids if i not in found_ids]
    audit('users.bulk_roles', target_type='users',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'requested': len(ids), 'action': action,
                   'domain_id': domain_id, 'domain_name': domain.name,
                   'role_id': role_id, 'role_name': role.name,
                   'changed': changed, 'not_found': not_found,
                   'results': results})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'changed': changed, 'not_found': not_found,
                    'results': results})


@admin_bp.route('/api/system/disk', methods=['GET'])
@login_required
def api_system_disk():
    """Disk-monitor status. Superadmin only -- exposes free-space details
    and the configured warn/block thresholds, plus a `blocking` flag the
    UI can use to surface a banner when uploads are disabled."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import disk_monitor, settings as _settings
    snap = disk_monitor.current_snapshot()
    blocking, _ = disk_monitor.is_blocking_uploads()
    return jsonify({
        'status':        'success',
        'snapshot':      snap,
        'warn_pct':      _settings.effective_value('disk.warn_pct') or 80,
        'block_pct':     _settings.effective_value('disk.block_uploads_pct') or 95,
        'blocking':      blocking,
    })


@admin_bp.route('/api/audit/retention/purge', methods=['POST'])
@login_required
def api_audit_retention_purge():
    """Run the audit-log retention sweep on demand. Superadmin only.
    Returns {action: deleted_count} for the rows pruned in this pass.
    The sweep itself walks every scope (global + per-tenant overrides);
    `?domain_id=N` is accepted for symmetry with the GET but the sweep
    isn't restricted -- it's cheap and per-tenant scopes share the job."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import audit_retention
    result = audit_retention.purge_now()
    return jsonify({
        'status':           'success',
        'deleted_by_action': result,
        'total':             sum(result.values()),
    })


@admin_bp.route('/admin/audit-retention')
@login_required
def admin_audit_retention_page():
    if not _is_superadmin():
        from flask import abort
        abort(403)
    return render_template('admin_audit_retention.html')


def _parse_scope_domain_id():
    """Parse `?domain_id=N` for the retention API. Empty / missing / 'null'
    means global scope. Returns (domain_id_or_None, error_response_or_None)."""
    raw = (request.args.get('domain_id') or '').strip()
    if raw in ('', 'null', 'None'):
        return None, None
    try:
        did = int(raw)
    except ValueError:
        return None, (jsonify({'status': 'error',
                               'message': 'domain_id must be an integer or null'}), 400)
    if did <= 0:
        return None, (jsonify({'status': 'error',
                               'message': 'domain_id must be > 0'}), 400)
    return did, None


@admin_bp.route('/api/audit/retention', methods=['GET'])
@login_required
def api_audit_retention_get():
    """Snapshot of current audit-retention configuration + per-action row counts.
    Superadmin only -- the action histogram leaks tenant activity volume.

    Query params:
      domain_id   integer or null  -- scope to inspect (None = global)
                                      When set, config values reflect the
                                      effective values for that tenant
                                      (override > global > builtin) and the
                                      action histogram is filtered to rows
                                      belonging to that tenant."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    scope_did, err = _parse_scope_domain_id()
    if err:
        return err
    import settings as _settings
    from models import AuditLog, SystemSetting, Domain
    from sqlalchemy import func
    from tenant_filter import bypass_tenant_filter

    def _ev(key):
        return _settings.effective_value(key, domain_id=scope_did)

    cfg = {
        'enabled':              bool(_ev('audit.retention.enabled')),
        'default_days':         int(_ev('audit.retention.default_days') or 0),
        'overrides':            _ev('audit.retention.overrides') or {},
        'purge_interval_hours': int(_ev('audit.retention.purge_interval_hours') or 24),
        'batch_size':           int(_ev('audit.retention.batch_size') or 5000),
    }

    # Surface which keys are explicitly overridden at this scope vs. inherited.
    overridden_at_scope = []
    if scope_did is not None:
        with bypass_tenant_filter():    # tenant-ok: scope inspection
            overridden_at_scope = [
                r[0] for r in (
                    db.session.query(SystemSetting.key)
                    .filter(SystemSetting.domain_id == scope_did,
                            SystemSetting.key.like('audit.retention.%'))
                    .all())]

    with bypass_tenant_filter():
        action_q = db.session.query(AuditLog.action, func.count(AuditLog.id))
        total_q  = db.session.query(func.count(AuditLog.id))
        if scope_did is not None:
            action_q = action_q.filter(AuditLog.domain_id == scope_did)
            total_q  = total_q.filter(AuditLog.domain_id == scope_did)
        rows = (action_q.group_by(AuditLog.action)
                .order_by(func.count(AuditLog.id).desc())
                .limit(50).all())
        total = total_q.scalar() or 0

        # Domain list with row counts so the UI can render a scope picker
        # that shows which tenants have audit traffic / overrides.
        domains_q = (db.session.query(
                        Domain.id, Domain.name,
                        func.count(AuditLog.id).label('rows'))
                     .outerjoin(AuditLog, AuditLog.domain_id == Domain.id)
                     .group_by(Domain.id, Domain.name)
                     .order_by(Domain.name.asc()))
        override_domain_ids = {
            r[0] for r in (
                db.session.query(SystemSetting.domain_id)
                .filter(SystemSetting.key.like('audit.retention.%'))
                .filter(SystemSetting.domain_id.isnot(None))
                .distinct().all())
        }
        domains = [{'id': did, 'name': name, 'rows': int(rc or 0),
                    'has_override': did in override_domain_ids}
                   for did, name, rc in domains_q.all()]

    return jsonify({
        'status':  'success',
        'scope':   {'domain_id': scope_did,
                    'overridden_keys': overridden_at_scope},
        'config':  cfg,
        'total':   total,
        'by_action': [{'action': a, 'count': c} for a, c in rows],
        'domains':  domains,
    })


# =============================================================================
# Schedule conflict visualiser
# Tenant-scoped read-only report; any logged-in user with schedule.read can
# view conflicts for their current tenant. Superadmins implicitly see the
# tenant they're switched into.
# =============================================================================
@admin_bp.route('/admin/schedule-conflicts')
@login_required
def admin_schedule_conflicts_page():
    from permissions import has_permission
    if not (_is_superadmin() or has_permission(current_user, 'schedule.read')):
        from flask import abort
        abort(403)
    return render_template('admin_schedule_conflicts.html')


@admin_bp.route('/api/schedule-conflicts', methods=['GET'])
@login_required
def api_schedule_conflicts():
    from permissions import has_permission
    if not (_is_superadmin() or has_permission(current_user, 'schedule.read')):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    try:
        days = int(request.args.get('days', '7'))
    except ValueError:
        return jsonify({'status': 'error',
                        'message': 'days must be an integer'}), 400
    from schedule_conflicts import compute_conflicts
    report = compute_conflicts(days_ahead=days)
    report['status'] = 'success'
    return jsonify(report)


# =============================================================================
# System Health -- one-stop superadmin overview combining disk, jobs,
# scheduled tasks, display online ratio, and append-only-log totals.
# Pure read-only aggregation; safe to poll from the dashboard at low rates.
# =============================================================================

@admin_bp.route('/admin/system-health')
@login_required
def admin_system_health_page():
    if not _is_superadmin():
        from flask import abort
        abort(403)
    return render_template('admin_system_health.html')


@admin_bp.route('/api/system/health', methods=['GET'])
@login_required
def api_system_health():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import platform, time as _time
    from sqlalchemy import func
    from models import Display, AuditLog
    from utils import is_online

    out = {'status': 'success', 'generated_at': int(_time.time())}

    # Disk
    try:
        import disk_monitor, settings as _settings
        snap = disk_monitor.current_snapshot()
        blocking, _reason = disk_monitor.is_blocking_uploads()
        out['disk'] = {
            'snapshot':  snap,
            'warn_pct':  _settings.effective_value('disk.warn_pct') or 80,
            'block_pct': _settings.effective_value('disk.block_uploads_pct') or 95,
            'blocking':  blocking,
        }
    except Exception as e:
        out['disk'] = {'error': str(e)}

    # Jobs
    try:
        import jobs
        out['jobs'] = {
            'queue_size': jobs.queue_size(),
            'periodic':   jobs.periodic_jobs(),
        }
    except Exception as e:
        out['jobs'] = {'error': str(e)}

    # Displays online ratio (across all tenants for superadmin)
    try:
        with bypass_tenant_filter():
            all_displays = Display.query.with_entities(Display.last_ping).all()
        total = len(all_displays)
        online = sum(1 for (lp,) in all_displays if is_online(lp))
        out['displays'] = {'total': total, 'online': online}
    except Exception as e:
        out['displays'] = {'error': str(e)}

    # Audit log + Proof of Play totals
    try:
        with bypass_tenant_filter():
            audit_total = db.session.query(func.count(AuditLog.id)).scalar() or 0
        out['audit_log'] = {'total': audit_total}
    except Exception as e:
        out['audit_log'] = {'error': str(e)}

    try:
        from models import ProofOfPlay
        with bypass_tenant_filter():
            pop_total = db.session.query(func.count(ProofOfPlay.id)).scalar() or 0
        import settings as _settings
        out['proof_of_play'] = {
            'enabled': bool(_settings.effective_value('proof_of_play.enabled')),
            'total':   pop_total,
        }
    except Exception as e:
        out['proof_of_play'] = {'error': str(e)}

    out['system'] = {
        'python':   platform.python_version(),
        'platform': platform.platform(),
    }
    return jsonify(out)


# =============================================================================
# Display offline / recovery alerts (admin UI + test trigger)
# =============================================================================

@admin_bp.route('/admin/alerts')
@login_required
def admin_alerts_page():
    if not _is_superadmin():
        from flask import abort
        abort(403)
    return render_template('admin_alerts.html')


@admin_bp.route('/admin/alerts/settings')
@login_required
def admin_alert_settings_page():
    if not _is_superadmin():
        from flask import abort
        abort(403)
    from domains import domain_switcher_state
    ds = domain_switcher_state()
    return render_template('admin_alert_settings.html', tenant_mode=False,
                           active_domain_id=ds.get('current_id'),
                           administrable_domains=_administrable_domains())


@admin_bp.route('/admin/alerts/notifications')
@login_required
def admin_alert_notifications_page():
    if not _is_superadmin():
        from flask import abort
        abort(403)
    return render_template('admin_alert_notifications.html')


@admin_bp.route('/api/alerts/summary', methods=['GET'])
@login_required
def api_alerts_summary():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    from datetime import datetime, timedelta
    from sqlalchemy import or_
    import settings as _settings
    from models import AuditLog
    since = datetime.utcnow() - timedelta(hours=24)
    with bypass_tenant_filter():
        recent_rows = (AuditLog.query
                       .filter(AuditLog.timestamp >= since)
                       .filter(or_(AuditLog.action.like('alerts.%'),
                                   AuditLog.action.like('security.%'),
                                   AuditLog.action.like('disk.%')))
                       .all())
    counts = {}
    for row in recent_rows:
        counts[row.action] = counts.get(row.action, 0) + 1
    disk = {}
    try:
        import disk_monitor
        disk = disk_monitor.current_snapshot() or disk_monitor.probe_now() or {}
    except Exception:
        disk = {}
    warn_pct = int(_settings.effective_value('disk.warn_pct') or 80)
    block_pct = int(_settings.effective_value('disk.block_uploads_pct') or 95)
    used_pct = disk.get('used_pct')
    if used_pct is None:
        disk_state = 'unknown'
    elif used_pct >= block_pct:
        disk_state = 'critical'
    elif used_pct >= warn_pct:
        disk_state = 'warning'
    else:
        disk_state = 'ok'
    import alerts as _alerts
    return jsonify({
        'status': 'success',
        'summary': {
            'enabled': bool(_settings.effective_value('alerts.enabled')),
            'active_outages': len(_alerts._alert_state),
            'duplicate_clients_24h': counts.get('alerts.duplicate_client_blocked', 0),
            'login_blocks_24h': counts.get('alerts.login_rate_limited', 0),
            'failed_logins_24h': counts.get('security.login_failed', 0),
            'delivery_failures_24h': counts.get('alerts.delivery_failed', 0),
            'disk': {
                'state': disk_state,
                'used_pct': used_pct,
                'free_bytes': disk.get('free_bytes'),
                'path': disk.get('path'),
                'warn_pct': warn_pct,
                'block_pct': block_pct,
            },
        },
    })


# =============================================================================
# Operator self-tests (cheap, safe-to-run-on-prod diagnostics)
# =============================================================================

@admin_bp.route('/admin/selftest')
@login_required
def admin_selftest_page():
    if not _is_superadmin():
        from flask import abort
        abort(403)
    return render_template('admin_selftest.html')


@admin_bp.route('/api/selftest/run', methods=['POST'])
@login_required
def api_selftest_run():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import selftest as _selftest
    result = _selftest.run_all()
    audit('selftest.run', payload={'ok': result['ok'],
                                   'failed': [c['name'] for c in result['checks'] if not c['ok']]})
    return jsonify({'status': 'success', 'result': result})


# Alert keys editable at tenant scope (domain admins + superadmin per-tenant).
_TENANT_ALERT_CONFIG_KEYS = {
    'alerts.enabled': 'bool',
    'alerts.offline_threshold_min': 'int',
    'alerts.webhook_url': 'string',
    'alerts.duplicate_client_enabled': 'bool',
    'alerts.bad_login_enabled': 'bool',
    'alerts.security_event_throttle_min': 'int',
    'alerts.digest_enabled': 'bool',
    'alerts.digest_hour': 'int',
    'alerts.digest_only': 'bool',
}

# Superadmin-only at global scope (cross-tenant recipient routing).
_SUPERADMIN_GLOBAL_ALERT_KEYS = {
    'alerts.user_recipients': 'json',
}


def _can_manage_tenant_alerts(domain_id):
    """May read/write alert notification settings for one tenant."""
    if domain_id is None:
        return _is_superadmin()
    if _is_superadmin():
        return True
    from permissions import has_permission
    return (has_permission(current_user, 'domain.admin', domain_id=domain_id)
            or has_permission(current_user, 'settings.write', domain_id=domain_id)
            or has_permission(current_user, 'emergency.manage', domain_id=domain_id))


def _can_access_tenant_alerts_ui():
    """Tenant Alerts page: any tenant the user may configure."""
    if _is_superadmin():
        return True
    for did in _administrable_domain_ids():
        if _can_manage_tenant_alerts(did):
            return True
    return False


def _resolve_alert_domain_id():
    """Resolve tenant scope for alert APIs.

    Returns (domain_id, error_response). ``domain_id`` None means global
    (superadmin only). Tenant admins always resolve to an administrable tenant.
    """
    from tenant_filter import current_domain_id
    body = request.get_json(silent=True) or {}
    raw = request.args.get('domain_id', type=int)
    if raw is None and request.method in ('POST', 'PUT'):
        raw = body.get('domain_id')
        if raw is not None:
            try:
                raw = int(raw)
            except (TypeError, ValueError):
                return None, (jsonify({'status': 'error',
                                        'message': 'domain_id must be an integer'}), 400)

    if _is_superadmin():
        if raw is not None and not _can_manage_tenant_alerts(raw):
            return None, (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
        return raw, None

    admin_ids = _administrable_domain_ids()
    if not admin_ids:
        return None, (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
    if raw is not None:
        if raw not in admin_ids or not _can_manage_tenant_alerts(raw):
            return None, (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
        return raw, None
    from flask import session
    sess_raw = session.get('current_domain_id')
    try:
        sess_did = int(sess_raw) if sess_raw is not None else None
    except (TypeError, ValueError):
        sess_did = None
    if sess_did in admin_ids and _can_manage_tenant_alerts(sess_did):
        return sess_did, None
    did = current_domain_id()
    if did in admin_ids and _can_manage_tenant_alerts(did):
        return did, None
    return admin_ids[0], None


@admin_bp.route('/api/alerts/config', methods=['GET'])
@login_required
def api_alerts_config_get():
    domain_id, err = _resolve_alert_domain_id()
    if err:
        return err
    if not _can_manage_tenant_alerts(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    import settings as _settings
    import alerts as _alerts
    keys = list(_TENANT_ALERT_CONFIG_KEYS)
    cfg = {k: _settings.effective_value(k, domain_id=domain_id) for k in keys}
    cfg['alerts.smtp_password_set'] = bool(
        _settings.effective_value('alerts.smtp_password', domain_id=domain_id))
    cfg['tracking'] = len(_alerts._alert_state)
    if _is_superadmin() and domain_id is None:
        with bypass_tenant_filter():
            domains = Domain.query.order_by(Domain.name.asc()).all()
            super_users = (User.query
                           .filter(User.active == True, User.is_superadmin == True)
                           .order_by(User.username.asc()).all())
            role_rows = (UserDomainRole.query
                         .join(User, UserDomainRole.user_id == User.id)
                         .join(Domain, UserDomainRole.domain_id == Domain.id)
                         .filter(User.active == True)
                         .order_by(Domain.name.asc(), User.username.asc())
                         .all())
        tenant_users = {}
        seen = set()
        for row in role_rows:
            if not row.user or not row.domain:
                continue
            key = (row.domain_id, row.user_id)
            if key in seen:
                continue
            seen.add(key)
            tenant_users.setdefault(str(row.domain_id), []).append({
                'id': row.user_id,
                'username': row.user.username,
                'email': row.user.email,
            })
        cfg['recipient_options'] = {
            'domains': [{'id': d.id, 'name': d.name} for d in domains],
            'global_users': [{'id': u.id, 'username': u.username, 'email': u.email}
                             for u in super_users],
            'tenant_users': tenant_users,
            'alert_types': [{'id': k, 'label': v}
                            for k, v in _alerts.ALERT_TYPE_CHOICES.items()],
        }
    return jsonify({'status': 'success', 'config': cfg, 'domain_id': domain_id})


def _sanitize_alert_user_recipients(value):
    """Keep alert recipient assignments inside the user's allowed scope.

    Current alert configuration endpoints are superadmin-only, but this still
    validates the JSON so stale IDs or cross-tenant assignments are not saved.
    """
    if not isinstance(value, dict):
        value = {}
    raw_global = value.get('global_user_ids') or []
    raw_tenant = value.get('tenant_user_ids') or {}
    raw_types = value.get('alert_types') or {}
    if not isinstance(raw_tenant, dict):
        raw_tenant = {}
    if not isinstance(raw_types, dict):
        raw_types = {}
    allowed_types = {'display', 'security', 'disk', 'digest'}

    def _ids(seq):
        out = set()
        for item in seq if isinstance(seq, list) else []:
            try:
                out.add(int(item))
            except (TypeError, ValueError):
                pass
        return out

    global_ids = _ids(raw_global)
    tenant_ids = {}
    for did, ids in raw_tenant.items():
        try:
            domain_id = int(did)
        except (TypeError, ValueError):
            continue
        if domain_id > 0:
            tenant_ids[domain_id] = _ids(ids)

    with bypass_tenant_filter():
        valid_global = {
            u.id for u in User.query
            .filter(User.id.in_(global_ids), User.active == True,
                    User.is_superadmin == True)
            .all()
        } if global_ids else set()
        valid_domains = {
            d.id for d in Domain.query
            .filter(Domain.id.in_(tenant_ids.keys()))
            .all()
        } if tenant_ids else set()
        role_rows = (UserDomainRole.query
                     .join(User, UserDomainRole.user_id == User.id)
                     .filter(User.active == True,
                             UserDomainRole.domain_id.in_(valid_domains))
                     .all()) if valid_domains else []
    allowed_by_domain = {}
    for row in role_rows:
        allowed_by_domain.setdefault(row.domain_id, set()).add(row.user_id)
    alert_types = {}
    for uid in valid_global:
        key = f'global:{uid}'
        requested = raw_types.get(key)
        if isinstance(requested, list):
            selected = sorted({str(x) for x in requested} & allowed_types)
        else:
            selected = sorted(allowed_types)
        if selected:
            alert_types[key] = selected
    valid_tenant_map = {
        str(did): sorted(ids & allowed_by_domain.get(did, set()))
        for did, ids in tenant_ids.items()
        if did in valid_domains and (ids & allowed_by_domain.get(did, set()))
    }
    for did, ids in valid_tenant_map.items():
        for uid in ids:
            key = f'tenant:{did}:{uid}'
            requested = raw_types.get(key)
            if isinstance(requested, list):
                selected = sorted({str(x) for x in requested} & allowed_types)
            else:
                selected = sorted(allowed_types)
            if selected:
                alert_types[key] = selected
    return {
        'global_user_ids': sorted(valid_global),
        'tenant_user_ids': valid_tenant_map,
        'alert_types': alert_types,
    }


def _sanitize_tenant_alert_recipients(value, domain_id):
    """Tenant-admin recipient list: only active members of this tenant."""
    if not isinstance(value, dict):
        value = {}
    allowed_types = {'display', 'security', 'disk', 'digest'}

    def _ids(seq):
        out = set()
        for item in seq if isinstance(seq, list) else []:
            try:
                out.add(int(item))
            except (TypeError, ValueError):
                pass
        return out

    raw_types = value.get('alert_types') or {}
    if not isinstance(raw_types, dict):
        raw_types = {}
    with bypass_tenant_filter():
        allowed = {
            r.user_id for r in
            UserDomainRole.query.join(User, UserDomainRole.user_id == User.id)
            .filter(UserDomainRole.domain_id == int(domain_id),
                    User.active == True)
            .all()
        }
    valid_ids = sorted(_ids(value.get('user_ids')) & allowed)
    alert_types = {}
    for uid in valid_ids:
        key = f'tenant:{uid}'
        requested = raw_types.get(key)
        if isinstance(requested, list):
            selected = sorted({str(x) for x in requested} & allowed_types)
        else:
            selected = sorted(allowed_types)
        if selected:
            alert_types[key] = selected
    return {'user_ids': valid_ids, 'alert_types': alert_types}


@admin_bp.route('/api/alerts/tenant-recipients', methods=['GET'])
@login_required
def api_tenant_recipients_get():
    """Per-tenant notification recipients (tenant admins + superadmin)."""
    domain_id, err = _resolve_alert_domain_id()
    if err:
        return err
    if domain_id is None:
        return jsonify({'status': 'error',
                        'message': 'domain_id required'}), 400
    if not _can_manage_tenant_alerts(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import settings as _settings
    import alerts as _alerts
    cfg = _settings.effective_value('alerts.tenant_recipients',
                                    domain_id=domain_id) or {}
    with bypass_tenant_filter():
        rows = (UserDomainRole.query
                .join(User, UserDomainRole.user_id == User.id)
                .filter(UserDomainRole.domain_id == domain_id,
                        User.active == True)
                .order_by(User.username.asc())
                .all())
    users = [{'id': r.user.id,
              'username': r.user.username,
              'email': r.user.email}
             for r in rows if r.user]
    return jsonify({
        'status': 'success',
        'domain_id': domain_id,
        'recipients': {
            'user_ids': cfg.get('user_ids') or [],
            'alert_types': cfg.get('alert_types') or {},
        },
        'users': users,
        'alert_types': [{'id': k, 'label': v}
                        for k, v in _alerts.ALERT_TYPE_CHOICES.items()],
    })


@admin_bp.route('/api/alerts/tenant-recipients', methods=['POST'])
@login_required
def api_tenant_recipients_set():
    domain_id, err = _resolve_alert_domain_id()
    if err:
        return err
    if domain_id is None:
        return jsonify({'status': 'error',
                        'message': 'domain_id required'}), 400
    if not _can_manage_tenant_alerts(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import settings as _settings
    body = request.get_json(silent=True) or {}
    raw = body.get('recipients', body)
    cleaned = _sanitize_tenant_alert_recipients(raw, domain_id)
    _settings.set('alerts.tenant_recipients', cleaned,
                  domain_id=domain_id, value_type='json')
    audit('alerts.tenant_recipients_changed', target_type='settings',
          target_id=str(domain_id),
          payload={'user_count': len(cleaned.get('user_ids') or [])})
    return jsonify({'status': 'success',
                    'domain_id': domain_id,
                    'recipients': cleaned})


@admin_bp.route('/api/alerts/config', methods=['POST'])
@login_required
def api_alerts_config_set():
    domain_id, err = _resolve_alert_domain_id()
    if err:
        return err
    if not _can_manage_tenant_alerts(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    import settings as _settings
    body = request.get_json(silent=True) or {}
    allowed = dict(_TENANT_ALERT_CONFIG_KEYS)
    if _is_superadmin() and domain_id is None:
        allowed.update(_SUPERADMIN_GLOBAL_ALERT_KEYS)
    changed = []
    for k, vtype in allowed.items():
        if k not in body:
            continue
        v = body[k]
        if k == 'alerts.smtp_password' and v == '':
            continue
        try:
            if vtype == 'bool':
                v = bool(v)
            elif vtype == 'int':
                v = int(v)
            elif vtype == 'json' and k == 'alerts.user_recipients':
                v = _sanitize_alert_user_recipients(v)
            else:
                v = str(v)
            _settings.set(k, v, domain_id=domain_id, value_type=vtype)
            changed.append(k)
        except Exception as exc:
            return jsonify({'status': 'error',
                            'message': f'bad value for {k}: {exc}'}), 400
    audit('alerts.config_changed', target_type='settings',
          target_id=str(domain_id) if domain_id else None,
          payload={'keys': changed, 'domain_id': domain_id})
    return jsonify({'status': 'success', 'changed': changed, 'domain_id': domain_id})


@admin_bp.route('/api/alerts/test', methods=['POST'])
@login_required
def api_alerts_test():
    """Send a test email + webhook using the configured channels so the
    operator can confirm delivery without waiting for a real outage."""
    domain_id, err = _resolve_alert_domain_id()
    if err:
        return err
    if not _can_manage_tenant_alerts(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import alerts as _alerts
    subject = '[AISignX] Test alert'
    body = ('This is a test alert from the AISignX server. If you are '
            'reading this, your alert delivery is configured correctly.')
    payload = {'event': 'test_alert', 'text': body}
    ok_e, err_e = _alerts._send_email(subject, body, domain_id=domain_id)
    ok_w, err_w = _alerts._send_webhook(payload, domain_id=domain_id)
    audit('alerts.test_sent',
          target_id=str(domain_id) if domain_id else None,
          payload={'email_ok': ok_e, 'webhook_ok': ok_w,
                   'email_error': err_e, 'webhook_error': err_w,
                   'domain_id': domain_id})
    return jsonify({'status': 'success',
                    'email':   {'ok': ok_e, 'error': err_e},
                    'webhook': {'ok': ok_w, 'error': err_w}})


@admin_bp.route('/api/alerts/sweep', methods=['POST'])
@login_required
def api_alerts_sweep_now():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import alerts as _alerts
    res = _alerts.sweep_now()
    return jsonify({'status': 'success', 'result': res})


@admin_bp.route('/api/alerts/snoozes', methods=['GET'])
@login_required
def api_alerts_snoozes_list():
    """List every display that currently has alerts snoozed, with the
    expiry time and reason. Cross-tenant: superadmin-only."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import alerts as _alerts
    snoozed = _alerts.snoozes()
    if not snoozed:
        return jsonify({'status': 'success', 'snoozes': []})
    with bypass_tenant_filter():    # tenant-ok: cross-tenant snooze list
        rows = (Display.query
                .filter(Display.id.in_(list(snoozed.keys())))
                .with_entities(Display.id, Display.name, Display.location,
                               Display.domain_id)
                .all())
    info = _alerts.snooze_info()    # {id: {'until': ts, 'reason': str}}
    out = []
    for r in rows:
        meta = info.get(r.id, {})
        out.append({
            'id':            r.id,
            'name':          r.name,
            'location':      r.location,
            'domain_id':     r.domain_id,
            'snoozed_until': meta.get('until'),
            'reason':        meta.get('reason') or '',
        })
    out.sort(key=lambda x: x['snoozed_until'] or 0)
    return jsonify({'status': 'success', 'snoozes': out})


@admin_bp.route('/api/alerts/snoozes/<int:display_id>', methods=['DELETE'])
@login_required
def api_alerts_snooze_clear(display_id):
    """Clear an active snooze for one display."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import alerts as _alerts
    result = _alerts.snooze_display(display_id, 0)
    audit('alerts.snooze_cleared', target_type='display',
          target_id=str(display_id), payload={'via': 'admin'})
    return jsonify({'status': 'success', **result})


@admin_bp.route('/api/alerts/recent', methods=['GET'])
@login_required
def api_alerts_recent():
    """Recent alert-related audit rows (offline / recovered / snooze /
    config / test / delivery failures). Cross-tenant: superadmin-only."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    from models import AuditLog
    try:
        limit = max(1, min(200, int(request.args.get('limit', 50))))
    except (TypeError, ValueError):
        limit = 50
    with bypass_tenant_filter():    # tenant-ok: cross-tenant alert log view
        rows = (AuditLog.query
                .filter(AuditLog.action.like('alerts.%'))
                .order_by(AuditLog.id.desc())
                .limit(limit)
                .all())
        # Resolve actor + display names in batches to keep this cheap.
        user_ids    = {r.actor_user_id for r in rows if r.actor_user_id}
        display_ids = set()
        for r in rows:
            if r.target_type == 'display' and r.target_id:
                try:
                    display_ids.add(int(r.target_id))
                except (TypeError, ValueError):
                    pass
        users    = {u.id: u.username for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
        displays = {d.id: d.name for d in Display.query.filter(Display.id.in_(display_ids)).all()} if display_ids else {}

    out = []
    for r in rows:
        did = None
        if r.target_type == 'display' and r.target_id:
            try:
                did = int(r.target_id)
            except (TypeError, ValueError):
                did = None
        out.append({
            'id':           r.id,
            'timestamp':    r.timestamp.isoformat() if r.timestamp else None,
            'action':       r.action,
            'actor':        users.get(r.actor_user_id) if r.actor_user_id else None,
            'display_id':   did,
            'display_name': displays.get(did) if did else None,
            'payload':      r.payload or {},
        })
    return jsonify({'status': 'success', 'events': out})


@admin_bp.route('/api/alerts/schedules', methods=['GET'])
@login_required
def api_alerts_schedules_get():
    """Return the parsed auto-snooze schedule list."""
    domain_id, err = _resolve_alert_domain_id()
    if err:
        return err
    if not _can_manage_tenant_alerts(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import alerts as _alerts
    return jsonify({'status': 'success', 'schedules': _alerts._load_schedules(domain_id),
                    'domain_id': domain_id})


@admin_bp.route('/api/alerts/schedules', methods=['POST'])
@login_required
def api_alerts_schedules_set():
    """Replace the entire schedule list. Body: {"schedules": [ {...}, ... ]}.
    Validation is deliberately lenient — bad windows simply never match."""
    domain_id, err = _resolve_alert_domain_id()
    if err:
        return err
    if not _can_manage_tenant_alerts(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import json as _json
    import settings as _settings
    data = request.get_json(silent=True) or {}
    schedules = data.get('schedules')
    if not isinstance(schedules, list):
        return jsonify({'status': 'error', 'message': 'schedules must be a list'}), 400
    cleaned = []
    for s in schedules:
        if not isinstance(s, dict):
            continue
        cleaned.append({
            'display_id':    int(s['display_id']) if s.get('display_id') not in (None, '', 0) else None,
            'group_id':      int(s['group_id'])   if s.get('group_id')   not in (None, '', 0) else None,
            'days':          [int(d) for d in (s.get('days') or []) if str(d).isdigit() and 0 <= int(d) <= 6],
            'start_hm':      str(s.get('start_hm') or '00:00')[:5],
            'end_hm':        str(s.get('end_hm')   or '00:00')[:5],
            'tz_offset_min': int(s.get('tz_offset_min') or 0),
            'label':         (s.get('label') or '').strip()[:80],
        })
    _settings.set('alerts.auto_snooze_schedules', _json.dumps(cleaned),
                  domain_id=domain_id)
    audit('alerts.schedules_changed',
          target_id=str(domain_id) if domain_id else None,
          payload={'count': len(cleaned), 'domain_id': domain_id})
    return jsonify({'status': 'success', 'schedules': cleaned, 'domain_id': domain_id})


@admin_bp.route('/api/alerts/digest/run', methods=['POST'])
@login_required
def api_alerts_digest_run():
    """Force an immediate digest send (bypasses 'already sent today' check)."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import alerts as _alerts
    res = _alerts.digest_now(force=True)
    return jsonify({'status': 'success', 'result': res})


# =============================================================================
# Plugin signing administration (Phase 4)
#
# Endpoints (all superadmin-only):
#   GET  /api/plugin-signing/status           list signing state of every plugin
#   POST /api/plugin-signing/sign/<key>       sign one plugin in place
#   POST /api/plugin-signing/sign-all         sign every registered plugin
#   POST /api/plugin-signing/rotate-secret    generate a new local secret
# =============================================================================

@admin_bp.route('/api/plugin-signing/status', methods=['GET'])
@login_required
def api_plugin_signing_status():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    from plugin_system import list_plugins, _load_registry
    import plugin_signing
    _load_registry(force=True)
    rows = []
    for p in list_plugins():
        rows.append({
            'plugin_key':       p.get('key') or p.get('type'),
            'name':             p.get('name'),
            'version':          p.get('version'),
            'signature_status': p.get('signature_status'),
            'signature_detail': p.get('signature_detail'),
        })
    return jsonify({
        'status':              'success',
        'require_signed':      plugin_signing.require_signed(),
        'trusted_secret_count': len(plugin_signing.trusted_secrets()),
        'plugins':             rows,
    })


@admin_bp.route('/api/plugin-signing/sign/<plugin_key>', methods=['POST'])
@login_required
def api_plugin_signing_sign(plugin_key):
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    from plugin_system import _plugins_root, get_plugin_meta, _load_registry
    import plugin_signing
    meta = get_plugin_meta(plugin_key)
    if meta is None:
        return jsonify({'status': 'error',
                        'message': f'plugin {plugin_key!r} not found'}), 404
    plugin_dir = _plugins_root() / (meta.get('type') or plugin_key)
    if not plugin_dir.is_dir():
        return jsonify({'status': 'error',
                        'message': f'plugin folder missing: {plugin_dir}'}), 404
    try:
        result = plugin_signing.sign_plugin_in_place(plugin_dir)
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    _load_registry(force=True)
    audit('plugin.sign', target_type='plugin', target_id=plugin_key,
          payload=result)
    return jsonify({'status': 'success', 'result': result})


@admin_bp.route('/api/plugin-signing/sign-all', methods=['POST'])
@login_required
def api_plugin_signing_sign_all():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    from plugin_system import _plugins_root, list_plugins, _load_registry
    import plugin_signing
    _load_registry(force=True)
    signed, failed = [], []
    for p in list_plugins():
        key = p.get('key') or p.get('type')
        plugin_dir = _plugins_root() / (p.get('type') or key)
        if not plugin_dir.is_dir():
            failed.append({'plugin': key, 'error': 'folder missing'})
            continue
        try:
            r = plugin_signing.sign_plugin_in_place(plugin_dir)
            signed.append(r)
        except Exception as e:
            failed.append({'plugin': key, 'error': str(e)})
    _load_registry(force=True)
    audit('plugin.sign_all', target_type='plugin',
          payload={'signed': len(signed), 'failed': len(failed)})
    return jsonify({'status': 'success', 'signed': signed, 'failed': failed})


@admin_bp.route('/api/plugin-signing/rotate-secret', methods=['POST'])
@login_required
def api_plugin_signing_rotate_secret():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import secrets as _secrets
    import settings as _settings
    new_secret = _secrets.token_hex(32)
    _settings.set('plugin.signing.secret', new_secret,
                  _allow_unknown=True, value_type='string', is_sensitive=True)
    audit('plugin.signing.rotate_secret', target_type='plugin_signing',
          payload={'message': 'new local secret generated; existing '
                              'signatures will need re-signing or trust-list entry'})
    return jsonify({'status': 'success',
                    'message': 'new local secret generated; re-sign plugins or '
                               'add the previous secret to trust_list to '
                               'continue accepting old signatures'})


# =============================================================================
# Proof-of-Play administration (Phase 4, optional)
#
#   GET  /api/proof-of-play          list rows (tenant-scoped)
#   GET  /api/proof-of-play.csv      same, CSV export
#   POST /api/proof-of-play/purge    manual retention sweep (superadmin)
#
# Superadmins may use scope=all to view all tenants. Tenant admins see only
# tenants where they hold domain.admin (same scope as audit log).
# =============================================================================

def _pop_viewable_domain_ids():
    """Tenant ids the current user may view proof-of-play for."""
    if _is_superadmin():
        with bypass_tenant_filter():
            return [d.id for d in Domain.query.filter_by(is_active=True)
                    .order_by(Domain.name.asc()).all()]
    return _administrable_domain_ids()


def _can_access_proof_of_play():
    """May open the Proof of Play UI / APIs."""
    if _is_superadmin():
        return True
    return bool(_pop_viewable_domain_ids())


def _resolve_proof_of_play_scope():
    """Resolve tenant scope from query params.

    Returns (scope_all, domain_ids, error_response) where domain_ids is a
    list of allowed domain primary keys, or None when scope_all (no filter).
    """
    from tenant_filter import current_domain_id

    scope_all = (request.args.get('scope') == 'all') and _is_superadmin()
    domain_id_param = request.args.get('domain_id', type=int)

    if _is_superadmin():
        if scope_all:
            if domain_id_param is not None:
                return False, [domain_id_param], None
            return True, None, None
        if domain_id_param is not None:
            return False, [domain_id_param], None
        did = current_domain_id()
        if did is not None:
            return False, [did], None
        return False, [], None

    allowed = _pop_viewable_domain_ids()
    if not allowed:
        return False, [], (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
    if scope_all:
        return False, [], (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
    if domain_id_param is not None:
        if domain_id_param not in allowed:
            return False, [], (jsonify({'status': 'error', 'message': 'forbidden'}), 403)
        return False, [domain_id_param], None
    did = current_domain_id()
    if did in allowed:
        return False, [did], None
    if len(allowed) == 1:
        return False, allowed, None
    return False, allowed, None


def _parse_iso(s):
    if not s:
        return None
    from datetime import datetime as _dt
    try:
        return _dt.fromisoformat(str(s).replace('Z', '+00:00')).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _parse_display_ids():
    """Accept repeated display_id params or a comma-separated display_ids value."""
    raw = []
    for v in request.args.getlist('display_id'):
        if v not in (None, ''):
            raw.append(v)
    bulk = request.args.get('display_ids')
    if bulk:
        raw.extend(str(bulk).split(','))
    ids = []
    for v in raw:
        try:
            ids.append(int(v))
        except (TypeError, ValueError):
            continue
    return ids or None


def _proof_of_play_rows(*, scope_all, domain_ids, default_limit=1000, max_limit=10000):
    import proof_of_play as pop
    domain_id = domain_ids[0] if domain_ids and len(domain_ids) == 1 else None
    display_ids = _parse_display_ids()
    if display_ids and domain_ids:
        with bypass_tenant_filter():
            allowed_disp = {
                r[0] for r in (
                    db.session.query(Display.id)
                    .filter(Display.id.in_(display_ids),
                            Display.domain_id.in_(domain_ids))
                    .all())
            }
        display_ids = [i for i in display_ids if i in allowed_disp] or None
    rows = pop.query_events(
        since       = _parse_iso(request.args.get('since')),
        until       = _parse_iso(request.args.get('until')),
        display_ids = display_ids,
        item_type   = request.args.get('item_type'),
        plugin_key  = request.args.get('plugin_key'),
        limit       = max(1, min(int(request.args.get('limit', default_limit)), max_limit)),
        scope_all   = scope_all,
        domain_id   = domain_id,
        domain_ids  = domain_ids,
    )
    display_ids = sorted({r.display_id for r in rows if r.display_id})
    displays = {}
    if display_ids:
        with bypass_tenant_filter():       # tenant-ok: resolve display labels for PoP
            for d in Display.query.filter(Display.id.in_(display_ids)).all():
                displays[d.id] = d
    show_domain = scope_all or (domain_ids and len(domain_ids) > 1)
    domains = {}
    if show_domain:
        row_domain_ids = sorted({r.domain_id for r in rows if r.domain_id})
        if row_domain_ids:
            with bypass_tenant_filter():       # tenant-ok: PoP domain labels
                for dom in Domain.query.filter(Domain.id.in_(row_domain_ids)).all():
                    domains[dom.id] = dom.name
    events = []
    for r in rows:
        ev = r.to_dict()
        disp = displays.get(r.display_id)
        ev['display_name'] = disp.name if disp else None
        if show_domain:
            ev['domain'] = domains.get(r.domain_id)
        events.append(ev)
    return scope_all, pop.is_enabled(), rows, events, show_domain


@admin_bp.route('/admin/proof-of-play')
@login_required
@require_permission('audit.read')
def admin_proof_of_play_page():
    if not _can_access_proof_of_play():
        from flask import abort
        abort(403)
    domains = []
    if not _is_superadmin():
        with bypass_tenant_filter():
            ids = _pop_viewable_domain_ids()
            if ids:
                domains = [{'id': d.id, 'name': d.name}
                           for d in Domain.query.filter(Domain.id.in_(ids))
                           .order_by(Domain.name.asc()).all()]
    return render_template('admin_proof_of_play.html',
                           is_superadmin=_is_superadmin(),
                           administrable_domains=domains)


@admin_bp.route('/api/proof-of-play/filters', methods=['GET'])
@login_required
@require_permission('audit.read')
def api_proof_of_play_filters():
    if not _can_access_proof_of_play():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import proof_of_play as pop
    scope_all, domain_ids, err = _resolve_proof_of_play_scope()
    if err:
        return err
    domain_id = domain_ids[0] if domain_ids and len(domain_ids) == 1 else None
    opts = pop.filter_options(scope_all=scope_all, domain_id=domain_id,
                              domain_ids=domain_ids)
    return jsonify({
        'status': 'success',
        'scope':  'all' if scope_all else 'tenant',
        'item_types': opts['item_types'],
        'plugins': opts['plugins'],
    })


@admin_bp.route('/api/proof-of-play', methods=['GET'])
@login_required
@require_permission('audit.read')
def api_proof_of_play_list():
    if not _can_access_proof_of_play():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    scope_all, domain_ids, err = _resolve_proof_of_play_scope()
    if err:
        return err
    scope_all, enabled, rows, events, show_domain = _proof_of_play_rows(
        scope_all=scope_all, domain_ids=domain_ids)
    return jsonify({
        'status':  'success',
        'enabled': enabled,
        'scope':   'all' if scope_all else 'tenant',
        'show_domain': show_domain,
        'count':   len(rows),
        'events':  events,
    })


@admin_bp.route('/api/proof-of-play.csv', methods=['GET'])
@login_required
@require_permission('audit.read')
def api_proof_of_play_csv():
    if not _can_access_proof_of_play():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import csv, io
    from flask import Response
    scope_all, domain_ids, err = _resolve_proof_of_play_scope()
    if err:
        return err
    scope_all, _enabled, rows, events, show_domain = _proof_of_play_rows(
        scope_all=scope_all, domain_ids=domain_ids,
        default_limit=10000, max_limit=100000)
    include_domain = show_domain
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ['started_at', 'display_id', 'display_name', 'item_type', 'item_name',
              'media_id', 'playlist_id', 'plugin_key',
              'duration_ms', 'completed', 'server_received_at']
    if include_domain:
        header.insert(1, 'domain')
    w.writerow(header)
    for ev in events:
        row = [
            ev.get('started_at') or '',
            ev.get('display_id') or '',
            ev.get('display_name') or '',
            ev.get('item_type') or '',
            ev.get('item_name') or '',
            ev.get('media_id') or '',
            ev.get('playlist_id') or '',
            ev.get('plugin_key') or '',
            ev.get('duration_ms') if ev.get('duration_ms') is not None else '',
            '1' if ev.get('completed') else '0',
            ev.get('server_received_at') or '',
        ]
        if include_domain:
            row.insert(1, ev.get('domain') or ev.get('domain_id') or '')
        w.writerow(row)
    return Response(buf.getvalue(), mimetype='text/csv',
                    headers={'Content-Disposition':
                             'attachment; filename=proof_of_play.csv'})


@admin_bp.route('/api/proof-of-play/purge', methods=['POST'])
@login_required
@require_permission('audit.read')
def api_proof_of_play_purge():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import proof_of_play as pop
    deleted = pop.purge_now()
    audit('proof_of_play.purge', target_type='proof_of_play',
          payload={'deleted': deleted})
    return jsonify({'status': 'success', 'deleted': deleted})


@admin_bp.route('/api/proof-of-play/enable', methods=['POST'])
@login_required
@require_permission('audit.read')
def api_proof_of_play_enable():
    """One-click toggle for `proof_of_play.enabled`. Superadmin-only because
    enabling PoP starts collecting per-display playback evidence -- a
    governance-relevant change that should be audited."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import settings as _settings
    data = request.get_json(silent=True) or {}
    enabled = bool(data.get('enabled'))
    old = bool(_settings.get('proof_of_play.enabled', default=False))
    _settings.set('proof_of_play.enabled', enabled,
                  user_id=getattr(current_user, 'id', None))
    audit('proof_of_play.toggle', target_type='proof_of_play',
          payload={'from': old, 'to': enabled})
    return jsonify({'status': 'success', 'enabled': enabled})


# =============================================================================
# Role assignment - per-user (domain, role) memberships
#
# A user has zero or more UserDomainRole rows; each row says "this user
# holds role R inside domain D". The UI surfaces this through:
#
#   GET    /api/users/<id>/roles                      list assignments
#   POST   /api/users/<id>/roles                      add one (domain_id, role_id)
#   DELETE /api/users/<id>/roles/<assignment_id>      remove one
#
# Plus two read-only helpers the modal needs:
#
#   GET /api/roles?domain_id=N    roles assignable inside domain N
#                                 (system roles + domain-custom roles)
#
# Authorization
# -------------
# Superadmin can assign anything anywhere.
# Domain admins can assign roles WITHIN their own domain only -- they
# cannot grant memberships in other tenants, and they cannot change a
# user's is_superadmin flag (that's a User-level field, handled in the
# user PUT endpoint).
# =============================================================================

def _can_admin_domain(domain_id):
    """True iff current_user is superadmin OR holds domain.admin in domain_id."""
    if _is_superadmin():
        return True
    if domain_id is None:
        return False
    from permissions import has_permission
    return has_permission(current_user, 'domain.admin', domain_id=domain_id)


def _administrable_domain_ids():
    """Tenant ids the current user may manage roles in."""
    with bypass_tenant_filter():
        if _is_superadmin():
            return [d.id for d in Domain.query.filter_by(is_active=True)
                    .order_by(Domain.name.asc()).all()]
        from permissions import has_permission
        out = []
        for d in Domain.query.filter_by(is_active=True).order_by(Domain.name.asc()).all():
            if has_permission(current_user, 'domain.admin', domain_id=d.id):
                out.append(d.id)
        return out


def _administrable_domains():
    """[{id, name}, ...] for role-admin UI dropdowns."""
    ids = _administrable_domain_ids()
    if not ids:
        return []
    with bypass_tenant_filter():
        rows = Domain.query.filter(Domain.id.in_(ids)).order_by(Domain.name.asc()).all()
        return [{'id': d.id, 'name': d.name} for d in rows]


def _can_access_roles_admin():
    """Roles & permissions UI: superadmin only (tenant role design is global)."""
    return _is_superadmin()


def _can_access_users_admin():
    """User management: superadmin or domain.admin in at least one tenant."""
    return _is_superadmin() or bool(_administrable_domain_ids())


def _user_in_admin_scope(user):
    """True if a domain admin may manage this user (never superadmin accounts)."""
    if user is None or getattr(user, 'is_superadmin', False):
        return False
    if _is_superadmin():
        return True
    admin_ids = set(_administrable_domain_ids())
    if not admin_ids:
        return False
    if user.home_domain_id in admin_ids:
        return True
    return any(udr.domain_id in admin_ids for udr in user.domain_roles)


def _can_manage_user_id(user_id):
    if _is_superadmin():
        return True
    with bypass_tenant_filter():
        u = db.session.get(User, user_id)
        return _user_in_admin_scope(u)


def _users_for_admin_list():
    """Users visible on the admin user-management page."""
    with bypass_tenant_filter():
        if _is_superadmin():
            return User.query.order_by(User.username.asc()).all()
        admin_ids = _administrable_domain_ids()
        if not admin_ids:
            return []
        role_user_ids = {
            r[0] for r in (
                db.session.query(UserDomainRole.user_id)
                .filter(UserDomainRole.domain_id.in_(admin_ids))
                .distinct().all())
        }
        home_user_ids = {
            u.id for u in User.query.filter(
                User.home_domain_id.in_(admin_ids),
                User.is_superadmin == False,
            ).all()
        }
        user_ids = role_user_ids | home_user_ids
        if not user_ids:
            return []
        return (User.query.filter(User.id.in_(user_ids),
                                User.is_superadmin == False)
                .order_by(User.username.asc()).all())


def _role_rows_for_users(user_ids):
    """Build user_domains map for the users list template."""
    if not user_ids:
        return {}
    with bypass_tenant_filter():
        q = (UserDomainRole.query
             .join(Domain, UserDomainRole.domain_id == Domain.id)
             .filter(UserDomainRole.user_id.in_(user_ids)))
        if not _is_superadmin():
            admin_ids = _administrable_domain_ids()
            q = q.filter(UserDomainRole.domain_id.in_(admin_ids))
        role_rows = q.order_by(Domain.name.asc()).all()
    user_domains = {}
    for row in role_rows:
        user_domains.setdefault(row.user_id, []).append({
            'id': row.domain_id,
            'name': row.domain.name if row.domain else f'Tenant #{row.domain_id}',
            'role': row.role.name if row.role else None,
        })
    return user_domains


def _udr_to_dict(udr):
    """One assignment row, dereferenced for the UI."""
    return {
        'id':          udr.id,
        'user_id':     udr.user_id,
        'domain_id':   udr.domain_id,
        'domain_name': udr.domain.name if udr.domain else None,
        'role_id':     udr.role_id,
        'role_name':   udr.role.name if udr.role else None,
        'is_system_role': bool(udr.role and udr.role.is_system),
        'created_at':  udr.created_at.isoformat() if udr.created_at else None,
    }


@admin_bp.route('/api/users/<int:user_id>/roles', methods=['GET'])
@login_required
def api_user_roles(user_id):
    """List a user's domain memberships. Superadmin sees all; a domain
    admin sees only the assignments scoped to their own domain(s)."""
    with bypass_tenant_filter():    # tenant-ok: cross-domain user lookup
        u = db.session.get(User, user_id)
        if u is None:
            return jsonify({'status': 'error', 'message': 'user not found'}), 404
        rows = (UserDomainRole.query
                .filter_by(user_id=user_id)
                .order_by(UserDomainRole.domain_id.asc(),
                          UserDomainRole.role_id.asc()).all())

    if not _is_superadmin():
        if not _can_manage_user_id(user_id):
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403
        rows = [r for r in rows if _can_admin_domain(r.domain_id)]

    return jsonify({
        'status':       'success',
        'user':         {'id': u.id, 'username': u.username,
                          'is_superadmin': u.is_superadmin},
        'assignments':  [_udr_to_dict(r) for r in rows],
    })


@admin_bp.route('/api/users/<int:user_id>/roles', methods=['POST'])
@login_required
def api_user_role_add(user_id):
    """Add one (domain, role) assignment to a user. Body: {domain_id, role_id}."""
    data = request.get_json(silent=True) or {}
    try:
        domain_id = int(data['domain_id'])
        role_id   = int(data['role_id'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'status': 'error',
                        'message': 'domain_id and role_id are required integers'}), 400

    if not _can_admin_domain(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    with bypass_tenant_filter():    # tenant-ok: assignment management
        u = db.session.get(User, user_id)
        if u is None:
            return jsonify({'status': 'error', 'message': 'user not found'}), 404
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'domain not found'}), 404
        r = db.session.get(Role, role_id)
        if r is None:
            return jsonify({'status': 'error', 'message': 'role not found'}), 404
        # Roles must be either system roles (domain_id NULL) or scoped to
        # this exact domain. A custom role from domain X cannot be assigned
        # inside domain Y.
        if r.domain_id is not None and r.domain_id != domain_id:
            return jsonify({'status': 'error',
                            'message': 'role is not assignable in this domain'}), 400

        existing = UserDomainRole.query.filter_by(
            user_id=user_id, domain_id=domain_id, role_id=role_id).first()
        if existing is not None:
            return jsonify({'status': 'error',
                            'message': 'assignment already exists',
                            'assignment': _udr_to_dict(existing)}), 409

        udr = UserDomainRole(user_id=user_id, domain_id=domain_id, role_id=role_id)
        db.session.add(udr)
        db.session.commit()

        snap = _udr_to_dict(udr)

    audit('user.role_assign', target_type='user', target_id=str(user_id),
          payload={'domain_id': domain_id, 'role_id': role_id,
                   'domain_name': snap['domain_name'],
                   'role_name': snap['role_name']})
    return jsonify({'status': 'success', 'assignment': snap})


@admin_bp.route('/api/users/<int:user_id>/roles/<int:assignment_id>',
                methods=['DELETE'])
@login_required
def api_user_role_remove(user_id, assignment_id):
    """Remove one specific assignment. Refuses to remove the requester's
    own last superadmin-equivalent role within their currently-active
    domain -- avoids self-lockouts. (is_superadmin is handled separately
    in the user-update endpoint.)"""
    with bypass_tenant_filter():    # tenant-ok: assignment management
        udr = db.session.get(UserDomainRole, assignment_id)
        if udr is None or udr.user_id != user_id:
            return jsonify({'status': 'error', 'message': 'assignment not found'}), 404

        if not _can_admin_domain(udr.domain_id):
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

        # Self-lockout guard: don't let a non-superadmin domain admin remove
        # their own last domain.admin role in this domain. Superadmins are
        # exempt because they always have a way back in.
        if (current_user.id == user_id
                and not getattr(current_user, 'is_superadmin', False)):
            from permissions import has_permission
            # Count other rows that still grant domain.admin in this domain.
            others = (UserDomainRole.query
                      .filter(UserDomainRole.user_id == user_id,
                              UserDomainRole.domain_id == udr.domain_id,
                              UserDomainRole.id != udr.id).all())
            other_grants_admin = any(
                'domain.admin' in {p.key for p in (o.role.permissions or [])}
                for o in others)
            if (not other_grants_admin
                    and 'domain.admin' in {p.key for p in (udr.role.permissions or [])}):
                return jsonify({'status': 'error',
                                'message': 'refusing to remove your own last domain.admin role here'}), 409

        snap = _udr_to_dict(udr)
        db.session.delete(udr)
        db.session.commit()

    audit('user.role_revoke', target_type='user', target_id=str(user_id),
          payload={'domain_id': snap['domain_id'], 'role_id': snap['role_id'],
                   'domain_name': snap['domain_name'],
                   'role_name': snap['role_name']})
    return jsonify({'status': 'success'})


@admin_bp.route('/api/roles', methods=['GET'])
@login_required
def api_list_roles():
    """List roles assignable in a given domain. domain_id query string is
    required; result includes system roles (always) plus that domain's
    custom roles. Used by the role-assignment modal."""
    domain_id = request.args.get('domain_id', type=int)
    if domain_id is None:
        return jsonify({'status': 'error',
                        'message': 'domain_id query param required'}), 400
    if not _can_admin_domain(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    with bypass_tenant_filter():    # tenant-ok: role catalog read
        rows = (Role.query
                .filter((Role.domain_id == None) | (Role.domain_id == domain_id))
                .order_by(Role.is_system.desc(), Role.name.asc()).all())
        return jsonify({
            'status': 'success',
            'roles': [{'id': r.id, 'name': r.name, 'is_system': r.is_system,
                        'domain_id': r.domain_id,
                        'description': r.description,
                        'permission_count': len(r.permissions)} for r in rows],
        })


# =============================================================================
# Custom role builder - CRUD for domain-scoped roles
#
# System roles (`domain_id IS NULL`, `is_system=True`) are seeded by
# bootstrap and are immutable through the UI. Custom roles are scoped to
# exactly one domain, fully editable, and assignable inside that domain
# only (the assign endpoint already enforces the domain match).
#
#   GET    /admin/roles                       HTML page (superadmin)
#   GET    /api/permissions                   the permission catalog
#   GET    /api/roles/<id>                    one role + its permission keys
#   POST   /api/roles                         create custom role
#   PUT    /api/roles/<id>                    rename / re-describe / replace perms
#   DELETE /api/roles/<id>                    delete (refuses if assignments exist)
#
# Authorization
# -------------
# Role builder UI and custom-role CRUD are superadmin-only. Tenant admins
# assign existing system roles via the Users page (GET /api/roles?domain_id=).
# =============================================================================

@admin_bp.route('/admin/roles')
@login_required
def admin_roles_page():
    """Custom role builder — superadmin only."""
    from flask import abort
    if not _can_access_roles_admin():
        abort(403)
    from permissions import permission_groups
    from tenant_filter import current_domain_id
    admin_domains = _administrable_domains()
    default_domain_id = current_domain_id()
    if default_domain_id not in {d['id'] for d in admin_domains}:
        default_domain_id = admin_domains[0]['id'] if admin_domains else None
    return render_template(
        'admin_roles.html',
        permission_groups=permission_groups(for_superadmin=_is_superadmin()),
        roles_scope={
            'is_superadmin': _is_superadmin(),
            'domains': admin_domains,
            'default_domain_id': default_domain_id,
        },
    )


@admin_bp.route('/api/permissions', methods=['GET'])
@login_required
def api_list_permissions():
    """Permission catalog for the role builder (superadmin only)."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    from permissions import all_permissions, permission_groups
    tenant_scoped = request.args.get('tenant_scoped', '').lower() in ('1', 'true', 'yes')
    return jsonify({
        'status': 'success',
        'permissions': [{'key': k, 'description': d}
                        for k, d in all_permissions()],
        'groups': permission_groups(for_superadmin=not tenant_scoped),
    })


@admin_bp.route('/api/roles/catalog', methods=['GET'])
@login_required
def api_roles_catalog():
    """List system + custom roles for the roles admin page (superadmin only).

    Optional ?domain_id=N filters custom roles to one tenant.
    """
    if not _can_access_roles_admin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    allowed_ids = _administrable_domain_ids()
    domain_id = request.args.get('domain_id', type=int)

    if domain_id is not None:
        if domain_id not in allowed_ids:
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403
        domain_ids = [domain_id]
    else:
        domain_ids = list(allowed_ids)

    with bypass_tenant_filter():
        roles_out = []
        system_rows = (Role.query.filter_by(domain_id=None, is_system=True)
                       .order_by(Role.name.asc()).all())
        for r in system_rows:
            roles_out.append(_role_to_dict(r))

        if domain_ids:
            custom_rows = (Role.query.filter(Role.domain_id.in_(domain_ids),
                                             Role.is_system == False)
                           .order_by(Role.domain_id.asc(), Role.name.asc()).all())
            for r in custom_rows:
                roles_out.append(_role_to_dict(r))

    from tenant_filter import current_domain_id
    domains = _administrable_domains()
    default_domain_id = current_domain_id()
    if default_domain_id not in {d['id'] for d in domains}:
        default_domain_id = domains[0]['id'] if domains else None

    return jsonify({
        'status': 'success',
        'roles': roles_out,
        'domains': domains,
        'scope': {
            'is_superadmin': _is_superadmin(),
            'default_domain_id': default_domain_id,
        },
    })


def _role_to_dict(r, include_perms=False):
    out = {
        'id':           r.id,
        'name':         r.name,
        'description':  r.description,
        'domain_id':    r.domain_id,
        'domain_name':  r.domain.name if r.domain else None,
        'is_system':    bool(r.is_system),
        'permission_count': len(r.permissions),
    }
    if include_perms:
        out['permissions'] = sorted(p.key for p in r.permissions)
    return out


@admin_bp.route('/api/roles/<int:role_id>', methods=['GET'])
@login_required
def api_get_role(role_id):
    """Return one role with its permission keys. Domain admins can read
    any role assignable in their domain (system roles + their own
    custom roles)."""
    with bypass_tenant_filter():    # tenant-ok: role read
        r = db.session.get(Role, role_id)
        if r is None:
            return jsonify({'status': 'error', 'message': 'role not found'}), 404
        # System roles are universally readable; custom roles need
        # domain admin in the role's home domain.
        if not r.is_system and not _can_admin_domain(r.domain_id):
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403
        return jsonify({'status': 'success',
                        'role': _role_to_dict(r, include_perms=True)})


def _validate_role_name(name):
    """Names: 1-80 chars, no leading/trailing whitespace, not 'system' to
    avoid confusing labels in the UI."""
    if not isinstance(name, str):
        return None, 'name is required'
    n = name.strip()
    if not n:
        return None, 'name is required'
    if len(n) > 80:
        return None, 'name must be 80 characters or fewer'
    return n, None


def _resolve_perm_keys(keys):
    """Map a list of permission keys to Permission rows. Unknown keys
    cause a 400 with the bad ones listed -- much friendlier than
    silently dropping them."""
    from models import Permission
    from permissions import PERMISSIONS as _CATALOG
    if keys is None:
        return [], None
    if not isinstance(keys, list):
        return None, 'permissions must be a list of permission keys'
    bad = [k for k in keys if k not in _CATALOG]
    if bad:
        return None, f'unknown permission keys: {", ".join(bad)}'
    rows = Permission.query.filter(Permission.key.in_(keys)).all()
    return rows, None


@admin_bp.route('/api/roles', methods=['POST'])
@login_required
def api_create_role():
    """Create a custom role. Body:
        {name, description?, domain_id, permissions: [keys]}
    Superadmin only.
    """
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    name, err = _validate_role_name(data.get('name'))
    if err:
        return jsonify({'status': 'error', 'message': err}), 400
    try:
        domain_id = int(data['domain_id'])
    except (KeyError, TypeError, ValueError):
        return jsonify({'status': 'error',
                        'message': 'domain_id is required'}), 400
    if not _can_admin_domain(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    perms, err = _resolve_perm_keys(data.get('permissions') or [])
    if err:
        return jsonify({'status': 'error', 'message': err}), 400
    if not _is_superadmin():
        bad_sa = [p.key for p in perms if p.key in ('domain.create', 'domain.delete')]
        if bad_sa:
            return jsonify({'status': 'error',
                            'message': 'only superadmins may grant tenant create/delete'}), 400

    description = (data.get('description') or '').strip() or None

    with bypass_tenant_filter():    # tenant-ok: role admin
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'domain not found'}), 404
        # Name must be unique within the (domain, name) pair AND also
        # not collide with a system role name -- otherwise the role
        # picker would have ambiguous entries.
        existing = (Role.query
                    .filter(Role.name == name)
                    .filter((Role.domain_id == None) | (Role.domain_id == domain_id))
                    .first())
        if existing is not None:
            return jsonify({'status': 'error',
                            'message': f'a role named {name!r} already exists '
                                       'in this domain or as a system role'}), 409

        role = Role(name=name, description=description,
                    domain_id=domain_id, is_system=False)
        for p in perms:
            role.permissions.append(p)
        db.session.add(role)
        db.session.commit()
        snap = _role_to_dict(role, include_perms=True)

    audit('role.create', target_type='role', target_id=str(snap['id']),
          payload={'name': name, 'domain_id': domain_id,
                   'permissions': snap['permissions']})
    return jsonify({'status': 'success', 'role': snap})


@admin_bp.route('/api/roles/<int:role_id>', methods=['PUT'])
@login_required
def api_update_role(role_id):
    """Replace a custom role's name / description / permission set.
    System roles are read-only and rejected with 400. Superadmin only.
    """
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}

    with bypass_tenant_filter():    # tenant-ok: role admin
        role = db.session.get(Role, role_id)
        if role is None:
            return jsonify({'status': 'error', 'message': 'role not found'}), 404
        if role.is_system:
            return jsonify({'status': 'error',
                            'message': 'system roles are read-only'}), 400
        if not _can_admin_domain(role.domain_id):
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

        changes = {}
        if 'name' in data:
            new_name, err = _validate_role_name(data['name'])
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            if new_name != role.name:
                # Re-check uniqueness on rename.
                clash = (Role.query
                         .filter(Role.id != role.id, Role.name == new_name)
                         .filter((Role.domain_id == None)
                                 | (Role.domain_id == role.domain_id))
                         .first())
                if clash is not None:
                    return jsonify({'status': 'error',
                                    'message': f'a role named {new_name!r} already '
                                               'exists in this domain or as a system role'}), 409
                changes['name'] = (role.name, new_name)
                role.name = new_name

        if 'description' in data:
            new_desc = (data.get('description') or '').strip() or None
            if new_desc != role.description:
                changes['description'] = (role.description, new_desc)
                role.description = new_desc

        if 'permissions' in data:
            perms, err = _resolve_perm_keys(data['permissions'])
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            if not _is_superadmin():
                bad_sa = [p.key for p in perms if p.key in ('domain.create', 'domain.delete')]
                if bad_sa:
                    return jsonify({'status': 'error',
                                    'message': 'only superadmins may grant tenant create/delete'}), 400
            old_keys = sorted(p.key for p in role.permissions)
            new_keys = sorted(p.key for p in perms)
            if old_keys != new_keys:
                # Replace wholesale -- simpler than computing add/remove
                # diffs and the secondary table is small.
                role.permissions = list(perms)
                changes['permissions'] = (old_keys, new_keys)

        db.session.commit()
        snap = _role_to_dict(role, include_perms=True)

    if changes:
        audit('role.update', target_type='role', target_id=str(role_id),
              payload={'changes': {k: {'from': v[0], 'to': v[1]}
                                   for k, v in changes.items()}})
    return jsonify({'status': 'success', 'role': snap})


@admin_bp.route('/api/roles/<int:role_id>', methods=['DELETE'])
@login_required
def api_delete_role(role_id):
    """Delete a custom role. Refuses if any UserDomainRole row still
    references it. Superadmin only.
    """
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():    # tenant-ok: role admin
        role = db.session.get(Role, role_id)
        if role is None:
            return jsonify({'status': 'error', 'message': 'role not found'}), 404
        if role.is_system:
            return jsonify({'status': 'error',
                            'message': 'system roles cannot be deleted'}), 400
        if not _can_admin_domain(role.domain_id):
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

        in_use = UserDomainRole.query.filter_by(role_id=role_id).count()
        if in_use > 0:
            return jsonify({'status': 'error',
                            'message': f'role still assigned to {in_use} user(s); '
                                       'remove those assignments first',
                            'assignment_count': in_use}), 409

        snap = _role_to_dict(role, include_perms=True)
        db.session.delete(role)
        db.session.commit()

    audit('role.delete', target_type='role', target_id=str(role_id),
          payload=snap)
    return jsonify({'status': 'success'})


# =============================================================================
# Backup + restore
#
# Backups bundle the SQLite DB + uploads tree + plugins tree into a
# single .zip in `./backups/`. Created via online sqlite snapshot so no
# downtime is required. Restore validates the manifest, rolls existing
# data to .restore-<ts> sibling dirs, and extracts -- DB changes
# require a server restart to take effect.
#
#   GET    /admin/backups                            HTML page
#   GET    /api/backups                              list
#   POST   /api/backups                              create
#   GET    /api/backups/<filename>/download          stream zip
#   POST   /api/backups/<filename>/restore           restore (DB+uploads+plugins toggleable)
#   DELETE /api/backups/<filename>                   delete
#
# All operations require superadmin -- a backup is a complete dump of
# every tenant's data and a restore can roll the whole server back.
# =============================================================================

@admin_bp.route('/admin/backups')
@login_required
def admin_backups_page():
    if not _is_superadmin():
        from flask import abort
        abort(403)
    return render_template('admin_backups.html')


@admin_bp.route('/api/backups', methods=['GET'])
@login_required
def api_list_backups():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import backup
    return jsonify({
        'status': 'success',
        'backups': backup.list_backups(),
        'location': backup.configured_backup_location(),
    })


@admin_bp.route('/api/backups', methods=['POST'])
@login_required
def api_create_backup():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    include_uploads = bool(data.get('include_uploads', True))
    include_plugins = bool(data.get('include_plugins', True))
    note = (data.get('note') or '').strip()
    import backup
    try:
        out = backup.create_backup(include_uploads=include_uploads,
                                    include_plugins=include_plugins,
                                    source='manual',
                                    note=note)
    except FileNotFoundError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500
    audit('backup.create', target_type='backup', target_id=out.name,
          payload={'include_uploads': include_uploads,
                   'include_plugins': include_plugins,
                   'size_bytes': out.stat().st_size,
                   'note': note})
    return jsonify({'status': 'success', 'filename': out.name,
                    'size_bytes': out.stat().st_size})


@admin_bp.route('/api/backups/<path:filename>/download', methods=['GET'])
@login_required
def api_download_backup(filename):
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import backup
    from flask import send_file
    try:
        p = backup.get_backup_path(filename)
    except (ValueError, FileNotFoundError):
        return jsonify({'status': 'error', 'message': 'not found'}), 404
    audit('backup.download', target_type='backup', target_id=p.name,
          payload={'size_bytes': p.stat().st_size})
    return send_file(str(p), as_attachment=True, download_name=p.name,
                     mimetype='application/zip')


@admin_bp.route('/api/backups/<path:filename>', methods=['DELETE'])
@login_required
def api_delete_backup(filename):
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import backup
    try:
        backup.delete_backup(filename)
    except (ValueError, FileNotFoundError):
        return jsonify({'status': 'error', 'message': 'not found'}), 404
    audit('backup.delete', target_type='backup', target_id=filename, payload={})
    return jsonify({'status': 'success'})


@admin_bp.route('/api/backups/<path:filename>/restore', methods=['POST'])
@login_required
def api_restore_backup(filename):
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    # Confirmation token: refuse the restore unless the caller passes
    # `confirm=True` AND the literal filename. Avoids accidental clicks
    # in the UI rolling the entire server back.
    if not data.get('confirm') or data.get('confirm_filename') != filename:
        return jsonify({'status': 'error',
                        'message': 'restore requires confirm=true and '
                                   'confirm_filename matching the target'}), 400
    restore_uploads = bool(data.get('restore_uploads', True))
    restore_plugins = bool(data.get('restore_plugins', True))
    # Default to taking a pre-restore snapshot so the admin always has a
    # one-click rollback. Power users can opt out by passing false.
    pre_snapshot    = bool(data.get('pre_snapshot', True))

    import backup
    try:
        result = backup.restore_backup(filename,
                                        restore_uploads=restore_uploads,
                                        restore_plugins=restore_plugins,
                                        pre_snapshot=pre_snapshot)
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    except FileNotFoundError:
        return jsonify({'status': 'error', 'message': 'not found'}), 404
    except RuntimeError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

    # Audit lands on the OLD database (we're still talking to it through
    # SQLAlchemy's open connection); after restart, the audit row will
    # disappear. That's fine -- the new audit log is the source of
    # truth post-restore.
    audit('backup.restore', target_type='backup', target_id=filename,
          payload={'restore_uploads': restore_uploads,
                   'restore_plugins': restore_plugins,
                   'pre_snapshot': pre_snapshot,
                   'staged_paths': [str(p) for p in result.staged_paths]})
    return jsonify({'status': 'success', 'result': result.to_dict()})

# ---------------------------------------------------------------------------
# Backup schedule + location admin
#
#   GET  /api/backups/schedule                schedule + location settings
#   PUT  /api/backups/schedule                update schedule + location
#   POST /api/backups/run-now                 trigger an out-of-band scheduled run
# ---------------------------------------------------------------------------
_BACKUP_SETTING_KEYS = (
    'backup.location',
    'backup.schedule.enabled',
    'backup.schedule.interval_hours',
    'backup.schedule.include_uploads',
    'backup.schedule.include_plugins',
    'backup.schedule.retain',
)


@admin_bp.route('/api/backups/schedule', methods=['GET'])
@login_required
def api_backup_schedule_get():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    import settings as settings_module
    import backup
    out = {k: settings_module.effective_value(k) for k in _BACKUP_SETTING_KEYS}
    out['resolved_location'] = backup.configured_backup_location()
    return jsonify({'status': 'success', 'schedule': out})


@admin_bp.route('/api/backups/schedule', methods=['PUT'])
@login_required
def api_backup_schedule_put():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    import settings as settings_module
    changed = {}
    # Whitelisted writes only -- no settings injection from arbitrary keys.
    if 'location' in data:
        settings_module.set('backup.location', str(data.get('location') or ''))
        changed['backup.location'] = True
    if 'enabled' in data:
        settings_module.set('backup.schedule.enabled', bool(data.get('enabled')))
        changed['backup.schedule.enabled'] = True
    if 'interval_hours' in data:
        try:
            v = max(1, int(data.get('interval_hours') or 24))
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': 'interval_hours must be an integer >= 1'}), 400
        settings_module.set('backup.schedule.interval_hours', v)
        changed['backup.schedule.interval_hours'] = True
    if 'include_uploads' in data:
        settings_module.set('backup.schedule.include_uploads',
                            bool(data.get('include_uploads')))
        changed['backup.schedule.include_uploads'] = True
    if 'include_plugins' in data:
        settings_module.set('backup.schedule.include_plugins',
                            bool(data.get('include_plugins')))
        changed['backup.schedule.include_plugins'] = True
    if 'retain' in data:
        try:
            v = max(0, int(data.get('retain') or 0))
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': 'retain must be a non-negative integer'}), 400
        settings_module.set('backup.schedule.retain', v)
        changed['backup.schedule.retain'] = True

    audit('backup.schedule.update', target_type='settings',
          target_id='backup.schedule', payload={'changed': sorted(changed)})

    # Note: interval_hours changes take effect on the next scheduler restart
    # (the periodic job's cadence is captured at install time). Other changes
    # are read on every tick.
    return api_backup_schedule_get()


@admin_bp.route('/api/backups/run-now', methods=['POST'])
@login_required
def api_backup_run_now():
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    # Run synchronously so the UI can surface success/failure inline.
    # The scheduler module honors the same enabled/include/retain settings
    # so the result matches what the periodic run would have produced.
    import backup_scheduler
    info = backup_scheduler.run_now()
    if info is None:
        return jsonify({'status': 'error',
                        'message': 'scheduled backups are disabled or the run failed; '
                                   'check server logs'}), 400
    audit('backup.scheduled.run_now', target_type='backup',
          target_id=info.name if hasattr(info, 'name') else str(info), payload={})
    return jsonify({'status': 'success',
                    'filename': info.name if hasattr(info, 'name') else str(info)})

# =============================================================================
# Plugin policy admin (Phase 3 sandboxing)
#
#   GET    /admin/plugin-policy                          page (tenant admin + superadmin)
#   GET    /api/plugin-policy?domain_id=N                policy snapshot for tenant
#   PUT    /api/plugin-policy/<plugin_key>?domain_id=N   set enabled only
#
# Sandbox permission grants are fixed by each plugin's manifest at install
# time. This UI only toggles enabled/disabled per tenant. Default when no
# row exists: enabled=True, grants = whatever the plugin declares.
# =============================================================================

@admin_bp.route('/admin/plugin-policy')
@login_required
def admin_plugin_policy_page():
    if not _is_superadmin() and not _administrable_domain_ids():
        from flask import abort
        abort(403)
    return render_template('admin_plugin_policy.html',
                           is_superadmin=_is_superadmin(),
                           administrable_domains=_administrable_domains())


@admin_bp.route('/api/plugin-policy', methods=['GET'])
@login_required
def api_plugin_policy_get():
    """Snapshot of all plugins x policies for one domain. The plugin
    list comes from the registry (filesystem); the policy rows come
    from the DB. Plugins missing from the DB are reported with their
    default policy."""
    try:
        domain_id = int(request.args.get('domain_id', '0'))
    except ValueError:
        return jsonify({'status': 'error', 'message': 'invalid domain_id'}), 400
    if domain_id <= 0:
        return jsonify({'status': 'error', 'message': 'domain_id required'}), 400
    if not _can_admin_domain(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    from plugin_system import (list_plugins, list_permission_catalog,
                                resolve_plugin_policy)
    plugins = list_plugins()

    out = []
    for p in plugins:
        key = p.get('key') or p.get('type')
        pol = resolve_plugin_policy(key, domain_id)
        out.append({
            'plugin_key':           key,
            'name':                 p.get('name'),
            'version':              p.get('version'),
            'declared_permissions': list(p.get('declared_permissions') or []),
            'csp_origins':          list(p.get('csp_origins') or []),
            'enabled':              pol['enabled'],
            'granted_permissions':  pol['granted_permissions'],
        })
    return jsonify({
        'status':              'success',
        'domain_id':           domain_id,
        'permission_catalog':  list_permission_catalog(),
        'policies':            out,
    })


@admin_bp.route('/api/plugin-policy/<plugin_key>', methods=['PUT'])
@login_required
def api_plugin_policy_put(plugin_key):
    """Set enabled/disabled for one plugin in one domain. Body: {enabled: bool}

    Permission grants are not editable here — they come from the plugin
    manifest (NULL stored grant = all declared permissions).
    """
    try:
        domain_id = int(request.args.get('domain_id', '0'))
    except ValueError:
        return jsonify({'status': 'error', 'message': 'invalid domain_id'}), 400
    if domain_id <= 0:
        return jsonify({'status': 'error', 'message': 'domain_id required'}), 400
    if not _can_admin_domain(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    data = request.get_json(silent=True) or {}
    if 'granted_permissions' in data:
        return jsonify({'status': 'error',
                        'message': 'Permission grants are defined in the plugin '
                                   'manifest and cannot be changed per tenant'}), 400
    enabled = bool(data.get('enabled', True))

    from plugin_system import get_plugin_meta
    meta = get_plugin_meta(plugin_key)
    if meta is None:
        return jsonify({'status': 'error',
                        'message': f'plugin {plugin_key!r} not found'}), 404
    declared = list(meta.get('declared_permissions') or [])

    from models import DomainPluginPolicy
    with bypass_tenant_filter():    # tenant-ok: explicit domain_id, superadmin/domain admin gated above
        pol = (DomainPluginPolicy.query
                .filter_by(domain_id=domain_id, plugin_key=plugin_key)
                .first())
        if pol is None:
            pol = DomainPluginPolicy(domain_id=domain_id, plugin_key=plugin_key)
            db.session.add(pol)
        old = {'enabled': pol.enabled, 'granted_permissions': pol.granted_permissions}

        pol.enabled = enabled

        db.session.commit()
        snap = pol.to_dict()
        effective_granted = pol.granted_permissions

    audit('plugin_policy.update', target_type='plugin_policy',
          target_id=f'{domain_id}:{plugin_key}',
          payload={'from': old,
                   'to':   {'enabled': pol.enabled,
                            'granted_permissions': pol.granted_permissions}})

    # Phase 4: live-push the change to every connected display in this
    # domain so plugin iframes can re-init or be reloaded by the player.
    try:
        from display_player import push_plugin_policy_changed
        eff = (effective_granted if effective_granted is not None else declared)
        push_plugin_policy_changed(domain_id, plugin_key, {
            'enabled':            pol.enabled,
            'granted_permissions': eff,
        })
    except Exception:
        from logging_config import logger as _log
        _log.exception('plugin policy push notification failed')

    eff = (effective_granted if effective_granted is not None else declared)
    return jsonify({'status': 'success', 'policy': snap,
                    'effective_granted_permissions': eff})
