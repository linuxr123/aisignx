"""API token management — per-user tokens with granular scopes."""
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from models import ApiToken, Media, User, db
import secrets
from utils import require_admin, _hash_token
from audit import audit
from tenant_filter import bypass_tenant_filter, current_domain_id
from permissions import scopes_grantable_by_user

tokens_bp = Blueprint('tokens', __name__)

# Canonical list of API scopes — UI and validation reference this.
# Each entry: (scope_value, label, description)
ALL_SCOPES = [
    ('media:read',     'Media: Read',     'List, view, and download media items'),
    ('media:write',    'Media: Write',    'Upload, replace, edit, and delete media items'),
    ('playlist:read',  'Playlists: Read', 'List and view playlists and items'),
    ('playlist:write', 'Playlists: Write','Create, edit, and delete playlists and items'),
    ('display:read',   'Displays: Read',  'List and view displays'),
    ('display:write',  'Displays: Write', 'Create, edit, and delete displays'),
    ('group:read',     'Groups: Read',    'List and view display groups'),
    ('group:write',    'Groups: Write',   'Create, edit, and delete display groups'),
    ('schedule:read',  'Schedules: Read', 'List and view schedules'),
    ('schedule:write', 'Schedules: Write','Create, edit, and delete schedules'),
    ('emergency:read', 'Emergency: Read', 'View active emergency broadcasts'),
    ('emergency:write','Emergency: Write','Trigger and clear emergency broadcasts'),
]
VALID_SCOPES = {s[0] for s in ALL_SCOPES}


def _is_superadmin():
    return getattr(current_user, 'is_superadmin', False)


def _can_manage_user_tokens(target_user_id):
    """Superadmin or domain.admin for users in their tenant scope."""
    from admin import _can_manage_user_id
    return _can_manage_user_id(target_user_id)


def _parse_scopes(raw_scopes):
    if isinstance(raw_scopes, str):
        raw_scopes = [s.strip() for s in raw_scopes.split(',') if s.strip()]
    if not isinstance(raw_scopes, list):
        return []
    return [s for s in raw_scopes if s in VALID_SCOPES]


def _token_row_dict(tok):
    return {
        'id': tok.id,
        'name': tok.name,
        'scopes': tok.scopes,
        'domain_id': tok.domain_id,
        'created_at': tok.created_at.isoformat() if tok.created_at else None,
        'last_used_at': tok.last_used_at.isoformat() if tok.last_used_at else None,
        'media_id': tok.media_id,
        'preview': (tok.token_hash or '')[-8:],
        'revoked': bool(tok.revoked),
        'user_id': tok.user_id,
        'user': tok.user.username if tok.user else None,
    }


def _validate_token_create(target_user, domain_id, scope_list, media_id=None):
    """Return (error_message, http_status) or (None, None) on success."""
    if not scope_list:
        return 'At least one scope is required', 400
    grantable = scopes_grantable_by_user(current_user, domain_id)
    if not _is_superadmin():
        bad = [s for s in scope_list if s not in grantable]
        if bad:
            return f'You cannot grant scope(s): {", ".join(bad)}', 403
    if not getattr(target_user, 'is_service_account', False):
        user_grantable = scopes_grantable_by_user(target_user, domain_id)
        bad = [s for s in scope_list if s not in user_grantable]
        if bad:
            return (f'Token scopes exceed user permissions: {", ".join(bad)}. '
                    'Assign tenant roles first or use a service account.'), 400
    if media_id not in (None, ''):
        try:
            int(media_id)
        except (TypeError, ValueError):
            return 'media_id must be an integer or null', 400
    return None, None


@tokens_bp.route('/settings/api', methods=['GET'])
@login_required
@require_admin
def api_tools_page():
    """Legacy page — token management moved to User Management."""
    return redirect(url_for('admin.users'))


@tokens_bp.route('/api/token-scopes', methods=['GET'])
@login_required
@require_admin
def api_token_scopes():
    """Scopes the current admin may grant when creating a token."""
    try:
        domain_id = int(request.args.get('domain_id'))
    except (TypeError, ValueError):
        domain_id = current_domain_id()
    grantable = scopes_grantable_by_user(current_user, domain_id)
    scopes = [
        {'value': v, 'label': lbl, 'description': desc, 'grantable': v in grantable}
        for v, lbl, desc in ALL_SCOPES
    ]
    return jsonify({'status': 'success', 'scopes': scopes, 'domain_id': domain_id})


@tokens_bp.route('/api/users/<int:user_id>/tokens', methods=['GET'])
@login_required
@require_admin
def api_list_user_tokens(user_id):
    if not _can_manage_user_tokens(user_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():
        user = User.query.get_or_404(user_id)
        tokens = (ApiToken.query.filter_by(user_id=user.id, revoked=False)
                  .order_by(ApiToken.created_at.desc()).all())
    return jsonify({
        'status': 'success',
        'user': user.to_dict(),
        'tokens': [_token_row_dict(t) for t in tokens],
        'all_scopes': [{'value': v, 'label': l, 'description': d}
                       for v, l, d in ALL_SCOPES],
    })


@tokens_bp.route('/api/users/<int:user_id>/tokens', methods=['POST'])
@login_required
@require_admin
def api_create_user_token(user_id):
    if not _can_manage_user_tokens(user_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.json or {}
    with bypass_tenant_filter():
        target_user = User.query.get_or_404(user_id)
    try:
        domain_id = int(data.get('domain_id') or current_domain_id())
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'domain_id is required'}), 400

    name = (data.get('name') or 'api-token').strip()[:120]
    scope_list = _parse_scopes(data.get('scopes') or [])
    media_id = data.get('media_id')
    if media_id in ('', None):
        media_id = None

    err, err_status = _validate_token_create(target_user, domain_id, scope_list, media_id)
    if err:
        return jsonify({'status': 'error', 'message': err}), err_status
    if media_id not in (None, ''):
        media_id = int(media_id)
    else:
        media_id = None

    scopes = ','.join(scope_list)
    raw = secrets.token_urlsafe(32)
    tok = ApiToken(
        user_id=target_user.id,
        domain_id=domain_id,
        token_hash=_hash_token(raw),
        name=name,
        scopes=scopes,
        media_id=media_id,
    )
    with bypass_tenant_filter():
        db.session.add(tok)
        db.session.commit()
    audit('token.create', target_type='api_token', target_id=str(tok.id),
          payload={'name': name, 'scopes': scopes, 'media_id': media_id,
                   'owner_user_id': target_user.id,
                   'owner_username': target_user.username,
                   'is_service_account': bool(target_user.is_service_account)},
          domain_id=domain_id)
    return jsonify({
        'status': 'success',
        'token': raw,
        'token_row': _token_row_dict(tok),
    })


@tokens_bp.route('/api/users/<int:user_id>/tokens/<int:token_id>/revoke', methods=['POST'])
@login_required
@require_admin
def api_revoke_user_token(user_id, token_id):
    if not _can_manage_user_tokens(user_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():
        tok = ApiToken.query.filter_by(id=token_id, user_id=user_id).first_or_404()
        tok.revoked = True
        db.session.commit()
    audit('token.revoke', target_type='api_token', target_id=str(tok.id),
          payload={'name': tok.name, 'owner_user_id': user_id},
          domain_id=tok.domain_id)
    return jsonify({'status': 'success'})


# Legacy global token endpoints (backward compatibility)
@tokens_bp.route('/api/tokens', methods=['GET'])
@login_required
@require_admin
def api_list_tokens():
    with bypass_tenant_filter():
        tokens = ApiToken.query.filter_by(revoked=False).order_by(
            ApiToken.created_at.desc()).all()
    return jsonify({'status': 'success', 'tokens': [_token_row_dict(t) for t in tokens]})


@tokens_bp.route('/api/tokens', methods=['POST'])
@login_required
@require_admin
def api_create_token():
    """Legacy: create a token for user_id in body (defaults to current user)."""
    data = request.json or {}
    user_id = int(data.get('user_id') or current_user.id)
    with bypass_tenant_filter():
        target_user = User.query.get_or_404(user_id)
    if not _can_manage_user_tokens(user_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    try:
        domain_id = int(data.get('domain_id') or current_domain_id())
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'domain_id is required'}), 400
    name = (data.get('name') or 'automation').strip()[:120]
    scope_list = _parse_scopes(data.get('scopes') or [])
    media_id = data.get('media_id')
    err, err_status = _validate_token_create(target_user, domain_id, scope_list, media_id)
    if err:
        return jsonify({'status': 'error', 'message': err}), err_status
    if media_id not in (None, ''):
        media_id = int(media_id)
    else:
        media_id = None
    scopes = ','.join(scope_list)
    raw = secrets.token_urlsafe(32)
    tok = ApiToken(
        user_id=target_user.id,
        domain_id=domain_id,
        token_hash=_hash_token(raw),
        name=name,
        scopes=scopes,
        media_id=media_id,
    )
    with bypass_tenant_filter():
        db.session.add(tok)
        db.session.commit()
    audit('token.create', target_type='api_token', target_id=str(tok.id),
          payload={'name': name, 'scopes': scopes, 'media_id': media_id,
                   'owner_user_id': target_user.id},
          domain_id=domain_id)
    return jsonify({'status': 'success', 'token': raw, 'id': tok.id,
                    'name': name, 'scopes': scopes, 'media_id': media_id})


@tokens_bp.route('/api/tokens/<int:token_id>/revoke', methods=['POST'])
@login_required
@require_admin
def api_revoke_token(token_id):
    with bypass_tenant_filter():
        tok = ApiToken.query.filter_by(id=token_id).first_or_404()
    return api_revoke_user_token(tok.user_id, token_id)
