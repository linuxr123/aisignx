import json
import hashlib
from datetime import datetime, timedelta
from flask import jsonify, request, g
import time
from functools import wraps
from flask_login import current_user

from logging_config import logger
from models import Playlist, PlaylistItem, ApiToken, db

# Utility functions
def compute_playlist_version(playlist: Playlist) -> str:
    """Hash playlist structure and media/plugin details so clients detect changes."""
    try:
        if playlist.smart_rules:
            # Smart playlists synthesise items from the Media library at
            # resolve time. The hash must reflect the matched set so the
            # display reloads when tags change underneath it.
            items = playlist.resolved_items()
        else:
            items = PlaylistItem.query.filter_by(playlist_id=playlist.id).order_by(PlaylistItem.position).all()
        payload = []
        for it in items:
            payload.append({
                'id': it.id,
                'pos': int(it.position or 0),
                'dur': int(it.duration or 0),
                'ptype': it.plugin_type or '',
                'pcfg': it.plugin_config or {},
                'media_id': it.media_id or None,
                'media_fn': it.media.filename if it.media else None,
                'media_mt': it.media.media_type if it.media else None,
                # Per-item playback overrides MUST be in the hash, otherwise
                # changing a single item's transition / aspect / mute via the
                # editor (or via bulk-update without also touching the
                # playlist-wide default) leaves the version unchanged and the
                # SSE loop never pushes a reload to the displays.
                'trans': (it.transition or '').strip().lower(),
                'aspect': (it.aspect_mode or '').strip().lower(),
                'mute': bool(it.mute_audio),
                'cs': it.clip_start,
                'ce': it.clip_end,
                # Intentionally excluded: media_upd — metadata edits (name, duration,
                # description) should not interrupt a playing display. Only structural
                # changes (which file, which type, playlist order) trigger a reload.
            })
        base = {
            'playlist_id': playlist.id,
            'updated_at': playlist.updated_at.isoformat() if playlist.updated_at else None,
            'smart': bool(playlist.smart_rules),
            # Playlist-wide playback settings must be part of the version so
            # the display reloads when the operator changes the default
            # transition or video-audio override. Without these fields the
            # cached playlist payload kept playing with the old values.
            'default_transition': (playlist.default_transition or 'cut'),
            'video_audio_default': (getattr(playlist, 'video_audio_default', None) or 'inherit'),
            'random_transitions': (playlist.random_transitions or ''),
            'items': payload,
        }
        raw = json.dumps(base, sort_keys=True, default=str).encode('utf-8')
        return hashlib.sha256(raw).hexdigest()
    except Exception as e:
        logger.error(f"Failed to compute playlist version for {playlist.id}: {e}")
        # Fallback to timestamp to avoid “stuck” caches
        return str(int(time.time()))

def require_admin(f):
    """Allow access if the user is a superadmin OR holds 'domain.admin' in
    the active tenant. Replaces the old User.is_admin column gate.

    For new code prefer @require_permission('specific.key') from
    permissions.py; this decorator stays for backwards compat with routes
    that haven't been narrowed yet."""
    @wraps(f)
    def w(*args, **kwargs):
        if not getattr(current_user, 'is_authenticated', False):
            return jsonify({'status': 'error', 'message': 'Admin required'}), 403
        if getattr(current_user, 'is_superadmin', False):
            return f(*args, **kwargs)
        # Lazy import to avoid circular dep (permissions imports models).
        from permissions import has_permission
        if has_permission(current_user, 'domain.admin'):
            return f(*args, **kwargs)
        return jsonify({'status': 'error', 'message': 'Admin required'}), 403
    return w
    
def get_form_data(request_form, fields):
    """Extract specified fields from form data"""
    data = {}
    for field in fields:
        if field in request_form:
            data[field] = request_form[field]
    return data

def is_valid_password(password):
    """Validate password strength"""
    if len(password) < 8:
        return False
    
    # Check for at least one uppercase, one lowercase, one digit, and one special character
    has_upper = any(c.isupper() for c in password)
    has_lower = any(c.islower() for c in password)
    has_digit = any(c.isdigit() for c in password)
    has_special = any(c in '!@#$%^&*' for c in password)
    
    return has_upper and has_lower and has_digit and has_special

def api_auth_required(required_scopes=None):
    if required_scopes is None:
        required_scopes = []

    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            # Prefer Authorization header if present (so token restrictions apply even if session is active)
            auth = request.headers.get('Authorization', '')

            if auth.startswith('Bearer '):
                raw = auth.split(' ', 1)[1].strip()
                if not raw:
                    return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

                # Token lookup must bypass tenant filter -- we don't yet
                # know which tenant the request belongs to.
                from tenant_filter import bypass_tenant_filter, set_current_domain_id
                with bypass_tenant_filter():
                    token = ApiToken.query.filter_by(token_hash=_hash_token(raw), revoked=False).first()
                if not token or not token.user:
                    return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
                if not token.user.active:
                    return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401

                # Scope check
                for scope in required_scopes:
                    if not token.has_scope(scope):
                        return jsonify({'status': 'error', 'message': 'Forbidden: missing scope'}), 403

                # Activate the token's tenant for this request.
                set_current_domain_id(token.domain_id)

                # Attach token and principal for downstream checks
                g.api_token = token
                g.api_user = token.user
                token.last_used_at = datetime.now()
                with bypass_tenant_filter():
                    db.session.commit()

                # Generic per-token rate limit. Applied AFTER token
                # resolution so the bucket key is the token id (survives
                # NAT, isolates noisy tenants from each other) rather
                # than the source IP. Cookie-session callers fall through
                # to the IP-keyed branch below.
                from rate_limit import (_settings as _rl_settings,
                                         _check_and_consume as _rl_check,
                                         _refuse as _rl_refuse)
                _cfg = _rl_settings()
                if _cfg.get('ratelimit.enabled', True):
                    _lim = _cfg.get('ratelimit.api_per_min', 600) or 600
                    _key = f'tok:{token.id}:api'
                    _ok, _retry = _rl_check(_key, _lim, 60)
                    if not _ok:
                        return _rl_refuse(_key, _retry)
                return f(*args, **kwargs)

            # Fall back to session auth (admins via UI)
            if current_user.is_authenticated:
                if getattr(current_user, 'is_service_account', False):
                    return jsonify({'status': 'error',
                                    'message': 'Service accounts must use API tokens'}), 403
                g.api_token = None
                g.api_user = current_user
                # IP-keyed bucket for cookie callers. Same limit applies
                # but they share the bucket per source IP.
                from rate_limit import (_settings as _rl_settings,
                                         _check_and_consume as _rl_check,
                                         _client_ip as _rl_ip,
                                         _refuse as _rl_refuse)
                _cfg = _rl_settings()
                if _cfg.get('ratelimit.enabled', True):
                    _lim = _cfg.get('ratelimit.api_per_min', 600) or 600
                    _key = f'ip:{_rl_ip()}:api'
                    _ok, _retry = _rl_check(_key, _lim, 60)
                    if not _ok:
                        return _rl_refuse(_key, _retry)
                return f(*args, **kwargs)

            return jsonify({'status': 'error', 'message': 'Unauthorized'}), 401
        return wrapper
    return decorator

# Helper to enforce media binding on token
def _enforce_media_binding_or_403(target_media_id: int):
    tok = getattr(g, 'api_token', None)
    if tok and tok.media_id and int(tok.media_id) != int(target_media_id):
        return jsonify({
            'status': 'error',
            'message': f'Token is restricted to media_id={tok.media_id} and cannot access media_id={target_media_id}'
        }), 403
    return None

def _hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()
    
def parse_date(val):
    """Convert a YYYY-MM-DD string to a date object, or None if invalid."""
    if not val:
        return None
    try:
        return datetime.strptime(val, "%Y-%m-%d").date()
    except Exception:
        return None

def parse_time(val):
    """Convert HH:MM or HH:MM:SS string to time object, or None if invalid."""
    if not val:
        return None
    parts = val.split(':')
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        return datetime.time(hour=h, minute=m, second=s)
    except Exception:
        return None

def is_online(last_ping, timeout_seconds=120):
    """
    Returns True if last_ping is within timeout_seconds of now (UTC).
    """
    if not last_ping:
        return False
    return (datetime.now() - last_ping) < timedelta(seconds=timeout_seconds)