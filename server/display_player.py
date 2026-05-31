"""
display_player.py
=================
Browser-based clientless display player.

Routes
------
GET  /display/<token>          â€“ Full-screen player page (no login required)
GET  /display/<token>/events   â€“ SSE stream (pushes 'reload' on playlist change)
POST /display/<token>/ping     â€“ Keepalive from the player page
GET  /api/display/<token>/playlist â€“ JSON playlist for the player (same logic as
                                      the legacy /api/display/playlist but token
                                      is in the URL, not the Authorization header)
"""

import json
import time
import threading
from datetime import datetime
from flask import (
    Blueprint, render_template, request, jsonify,
    Response, stream_with_context, url_for, abort
)
from models import db, Display, Schedule, Playlist, PlaylistItem, EmergencyBroadcast, DisplayDiagnostic
from displays import _real_client_ip
from plugin_system import build_plugin_url, get_plugin_meta
from utils import compute_playlist_version
from logging_config import logger
import heartbeat

player_bp = Blueprint('player', __name__)


# ---------------------------------------------------------------------------
# Token-gated display lookup helper
#
# Token-gated player routes have no logged-in user, so the global tenant
# filter would force ``domain_id == -1`` and the lookup would miss every
# real row -> "Invalid token" 404. We resolve under bypass and then pin
# the request's tenant context to the display's domain so any subsequent
# tenant-scoped query/insert in this request stamps the right domain_id.
#
# Returns the Display, or None if the token is unknown.
# ---------------------------------------------------------------------------
def _resolve_display_by_token(token: str):
    from tenant_filter import bypass_tenant_filter, set_current_domain_id
    with bypass_tenant_filter():  # tenant-ok: token-gated player route has no logged-in user; tenant context is pinned from the resolved display below
        d = Display.query.filter_by(api_key=token).first()
    if d is not None:
        set_current_domain_id(d.domain_id)
    return d

# ---------------------------------------------------------------------------
# In-memory SSE connection registry
# { api_key: {'ip': str, 'client_id': str, 'last_seen': float, 'version': str} }
# ---------------------------------------------------------------------------
_connections: dict = {}
_connections_lock = threading.Lock()

# Per-connection push queues â€” used by push_emergency / push_emergency_clear
# so events reach displays immediately without waiting for the 2-second poll.
# { api_key: list[str] }   (raw SSE event strings)
_push_queues: dict = {}
_push_queues_lock = threading.Lock()

HEARTBEAT_INTERVAL = 25   # seconds between SSE heartbeats
PING_TIMEOUT       = 120  # seconds before a display is considered offline


def _register_connection(api_key: str, ip: str, version: str, client_id: str = ''):
    with _connections_lock:
        _connections[api_key] = {
            'ip': ip,
            'client_id': client_id,
            'last_seen': time.time(),
            'version': version,
        }
    with _push_queues_lock:
        _push_queues[api_key] = []


def _unregister_connection(api_key: str):
    with _connections_lock:
        _connections.pop(api_key, None)
    with _push_queues_lock:
        _push_queues.pop(api_key, None)


def _drain_push_queue(api_key: str):
    """Return and clear any pending push events for this connection."""
    with _push_queues_lock:
        events = _push_queues.get(api_key, [])
        if events:
            _push_queues[api_key] = []
        return events


def push_emergency(broadcast):
    """Queue an emergency event to connected displays in the broadcast's tenant."""
    from tenant_filter import bypass_tenant_filter
    payload = f'event: emergency\ndata: {json.dumps(broadcast.to_dict())}\n\n'
    with _push_queues_lock:
        keys = list(_push_queues.keys())
    if not keys:
        return 0
    try:
        with bypass_tenant_filter():    # tenant-ok: resolve api_keys by explicit domain_id
            rows = (Display.query
                    .filter(Display.api_key.in_(keys),
                            Display.domain_id == int(broadcast.domain_id))
                    .all())
    except Exception:
        logger.exception('push_emergency: display lookup failed')
        return 0
    queued = 0
    with _push_queues_lock:
        for disp in rows:
            if not broadcast.applies_to(disp):
                continue
            if disp.api_key in _push_queues:
                _push_queues[disp.api_key].append(payload)
                queued += 1
    return queued


def push_emergency_clear(broadcast):
    """Queue emergency_clear to displays that were targeted by this broadcast."""
    from tenant_filter import bypass_tenant_filter
    bid = broadcast.id if hasattr(broadcast, 'id') else int(broadcast)
    domain_id = getattr(broadcast, 'domain_id', None)
    target = getattr(broadcast, 'target', 'all')
    if domain_id is None:
        with bypass_tenant_filter():
            row = db.session.get(EmergencyBroadcast, bid)
        if row is None:
            return 0
        domain_id, target = row.domain_id, row.target
    payload = f'event: emergency_clear\ndata: {json.dumps({"id": bid})}\n\n'
    with _push_queues_lock:
        keys = list(_push_queues.keys())
    if not keys:
        return 0
    try:
        with bypass_tenant_filter():
            rows = (Display.query
                    .filter(Display.api_key.in_(keys),
                            Display.domain_id == int(domain_id))
                    .all())
    except Exception:
        logger.exception('push_emergency_clear: display lookup failed')
        return 0

    def _matches(disp):
        if target == 'all':
            return True
        if target == f'display:{disp.id}':
            return True
        if disp.group_id and target == f'group:{disp.group_id}':
            return True
        return False

    queued = 0
    with _push_queues_lock:
        for disp in rows:
            if not _matches(disp):
                continue
            if disp.api_key in _push_queues:
                _push_queues[disp.api_key].append(payload)
                queued += 1
    return queued


def _resolved_playlist_version(playlist, schedule_id: int) -> str:
    """Version string that changes when either the playlist or winning schedule changes."""
    import hashlib
    base = compute_playlist_version(playlist)
    return hashlib.sha256(f'{base}:{int(schedule_id)}'.encode()).hexdigest()


def push_playlist_reload(display) -> bool:
    """Queue an immediate SSE reload with fresh playlist data for one display."""
    if not display or not getattr(display, 'api_key', None):
        return False
    key = display.api_key
    with _push_queues_lock:
        if key not in _push_queues:
            return False
    try:
        data = _resolve_playlist(display)
    except Exception:
        logger.exception('push_playlist_reload: resolve failed for %s', key)
        return False
    version = data['version'] if data else ''
    event = 'event: reload\ndata: ' + json.dumps({
        'version': version,
        'playlist': data,
    }) + '\n\n'
    with _push_queues_lock:
        if key in _push_queues:
            _push_queues[key].append(event)
            _touch_connection(key, version)
            return True
    return False


def displays_affected_by_schedule(schedule) -> list:
    """Displays that would use this schedule (direct, group, or inherited group)."""
    from groups import resolve_effective_group_ids
    if not schedule:
        return []
    if schedule.display_id:
        d = Display.query.get(schedule.display_id)
        return [d] if d else []
    if not schedule.group_id:
        return []
    gid = int(schedule.group_id)
    out = []
    for disp in Display.query.filter(Display.group_id.isnot(None)).all():
        try:
            if gid in resolve_effective_group_ids(disp):
                out.append(disp)
        except Exception:
            continue
    return out


def notify_schedule_playlist_reload(schedule) -> int:
    """Push playlist reload to displays affected by a schedule change."""
    if not schedule:
        return 0
    queued = 0
    seen = set()
    for disp in displays_affected_by_schedule(schedule):
        if disp.id in seen:
            continue
        seen.add(disp.id)
        if push_playlist_reload(disp):
            queued += 1
    if queued:
        logger.info('schedule change: pushed playlist reload to %s display(s) '
                    '(schedule=%s)', queued, schedule.id)
    return queued


def notify_domain_playlist_reload(domain_id=None) -> int:
    """Push playlist reload to every connected display in a tenant."""
    from tenant_filter import bypass_tenant_filter, current_domain_id
    did = domain_id if domain_id is not None else current_domain_id()
    if did is None:
        return 0
    queued = 0
    with bypass_tenant_filter():
        rows = Display.query.filter_by(domain_id=int(did)).all()
    for disp in rows:
        if push_playlist_reload(disp):
            queued += 1
    if queued:
        logger.info('playlist change: pushed reload to %s connected display(s) '
                    'in domain %s', queued, did)
    return queued


def push_command(api_key: str, action: str, payload: dict | None = None) -> bool:
    """Queue a one-off command (e.g. 'reboot', 'update') to a single display.
    Returns True if the display has an active SSE connection, False otherwise."""
    body = {'action': action}
    if payload:
        body.update(payload)
    event = f'event: command\ndata: {json.dumps(body)}\n\n'
    with _push_queues_lock:
        if api_key not in _push_queues:
            return False
        _push_queues[api_key].append(event)
        return True


def push_plugin_policy_changed(domain_id: int, plugin_key: str,
                               policy: dict) -> int:
    """Broadcast a policy change to every connected display in a domain.
    Plugin iframes can listen for the matching window 'message' from the
    parent player and re-init or reload themselves. Returns the number of
    displays the event was queued to.

    Resolution from api_key -> domain happens via a quick Display lookup;
    we tolerate misses (display rows churn during deletes).
    """
    from models import Display
    from tenant_filter import bypass_tenant_filter
    body = {
        'plugin_key':         plugin_key,
        'enabled':            bool(policy.get('enabled', True)),
        'granted_permissions': policy.get('granted_permissions') or [],
    }
    event = f'event: plugin_policy\ndata: {json.dumps(body)}\n\n'
    queued = 0
    with _push_queues_lock:
        keys = list(_push_queues.keys())
    if not keys:
        return 0
    try:
        with bypass_tenant_filter():    # tenant-ok: cross-tenant filter to find affected api_keys
            rows = (Display.query
                    .filter(Display.api_key.in_(keys),
                            Display.domain_id == int(domain_id))
                    .with_entities(Display.api_key)
                    .all())
        affected = {r[0] for r in rows}
    except Exception:
        logger.exception('push_plugin_policy_changed: lookup failed')
        return 0
    if not affected:
        return 0
    with _push_queues_lock:
        for k in affected:
            if k in _push_queues:
                _push_queues[k].append(event)
                queued += 1
    return queued


def _touch_connection(api_key: str, version: str = None):
    with _connections_lock:
        if api_key in _connections:
            _connections[api_key]['last_seen'] = time.time()
            if version:
                _connections[api_key]['version'] = version


def _is_connection_alive(api_key: str) -> bool:
    with _connections_lock:
        conn = _connections.get(api_key)
        if not conn:
            return False
        return (time.time() - conn['last_seen']) < PING_TIMEOUT


def _connection_ip(api_key: str) -> str | None:
    with _connections_lock:
        conn = _connections.get(api_key)
        return conn['ip'] if conn else None


def _connection_client_id(api_key: str) -> str | None:
    with _connections_lock:
        conn = _connections.get(api_key)
        return conn.get('client_id') if conn else None


def _client_id_from_request(data: dict | None = None) -> str:
    client_id = (request.args.get('client_id') or '').strip()
    if not client_id and data:
        client_id = str(data.get('client_id') or '').strip()
    return client_id[:128]


def _token_conflict(api_key: str, client_ip: str, client_id: str = '') -> bool:
    """Return True when another live client is using this display token."""
    if not _is_connection_alive(api_key):
        return False
    existing_client_id = _connection_client_id(api_key)
    if existing_client_id and client_id and existing_client_id != client_id:
        return True
    existing_ip = _connection_ip(api_key)
    return bool(existing_ip and existing_ip != client_ip)


def _report_duplicate_client_blocked(display: Display, client_ip: str,
                                     client_id: str = '', source: str = ''):
    """Log/notify when a display token is being used by more than one client."""
    try:
        import alerts as _alerts
        import settings as _settings
        if not bool(_settings.effective_value('alerts.duplicate_client_enabled')):
            return
        existing_ip = _connection_ip(display.api_key)
        existing_client_id = _connection_client_id(display.api_key)
        name = display.name or f'Display #{display.id}'
        subject = f'[AISignX] Duplicate client blocked: {name}'
        body = (
            f'A second client was blocked from using the same display API key.\n\n'
            f'Display ID : {display.id}\n'
            f'Display    : {name}\n'
            f'Existing IP: {existing_ip or "n/a"}\n'
            f'New IP     : {client_ip or "n/a"}\n'
            f'Source     : {source or "player"}\n'
        )
        _alerts.notify_event(
            'duplicate_client_blocked',
            subject,
            body,
            target_type='display',
            target_id=display.id,
            domain_id=display.domain_id,
            throttle_key=f'duplicate-client:{display.id}:{client_id or client_ip}',
            payload={
                'display_id': display.id,
                'display_name': name,
                'existing_ip': existing_ip,
                'new_ip': client_ip,
                'existing_client_id': existing_client_id,
                'new_client_id': client_id,
                'source': source,
            },
        )
    except Exception as exc:
        logger.warning(f'duplicate client alert failed for display={display.id}: {exc}')


def _effective_show_media_buttons(display) -> bool:
    """Manual media controls are disabled whenever sync playback is active."""
    try:
        from sync_playback import group_sync_playback_active
        if group_sync_playback_active(display):
            return False
    except Exception:
        pass
    return bool(display.show_media_buttons)


# ---------------------------------------------------------------------------
# Playlist resolution helper (shared by player page + SSE + JSON API)
# ---------------------------------------------------------------------------
def _resolve_playlist(display) -> dict | None:
    """
    Evaluate schedules for *display* and return a full playlist dict,
    or None if no schedule/playlist is found.
    """
    now = datetime.now()
    today = now.date()
    current_time = now.time()

    # Schedule inheritance: walk up through ancestor groups so a
    # schedule attached to a parent group covers every descendant.
    from groups import resolve_effective_group_ids
    effective_group_ids = resolve_effective_group_ids(display)

    potential = []
    potential.extend(
        Schedule.query.filter(
            Schedule.is_active == True,
            Schedule.display_id == display.id
        ).order_by(Schedule.priority.desc(), Schedule.id.asc()).all()
    )
    if effective_group_ids:
        potential.extend(
            Schedule.query.filter(
                Schedule.is_active == True,
                Schedule.group_id.in_(effective_group_ids)
            ).order_by(Schedule.priority.desc(), Schedule.id.asc()).all()
        )

    valid = []
    for sched in potential:
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
        valid.append(sched)

    if not valid:
        # Fallback: any schedule for this display or any of its
        # effective groups. Same inheritance chain.
        fb_filter = Schedule.display_id == display.id
        if effective_group_ids:
            fb_filter = fb_filter | Schedule.group_id.in_(effective_group_ids)
        fallback = Schedule.query.filter(fb_filter).first()
        if fallback:
            valid = [fallback]
        else:
            return None

    chosen = valid[0]
    playlist = Playlist.query.get(chosen.playlist_id)
    if not playlist:
        return None
    raw_items = playlist.resolved_items()
    if not raw_items:
        return None

    items_sorted = sorted(raw_items, key=lambda x: x.position)
    # Per-display capability filter: drop items the client can't render
    # (e.g. webm video on a player that only declares h264). "Unknown
    # capability" is treated as compatible -- see capabilities.py.
    #
    # Synchronized groups must see identical item lists: wall-clock sync
    # shares one anchor + duration table; different capability drops per
    # display would desync indices instantly. Skip filtering when sync is on.
    from capabilities import filter_items_for_display, filter_items_for_sync_group
    from media_duration import playlist_item_duration_seconds
    from sync_playback import group_sync_playback_active
    if group_sync_playback_active(display):
        items_sorted, _skipped = filter_items_for_sync_group(items_sorted, display)
    else:
        items_sorted, _skipped = filter_items_for_display(items_sorted, display)
    if not items_sorted:
        return None
    # Resolve effective per-item transition once, server-side, so the player
    # only needs a single field. Item override wins; otherwise the playlist
    # default applies; 'random' is forwarded so the player can pick per slide.
    pl_default_transition = (playlist.default_transition or 'cut').lower()
    items_data = []
    for item in items_sorted:
        # Schema default for item.transition is 'cut'; treat that as
        # "inherit" so users who never opened the per-item editor still
        # get the playlist default.
        raw_t = (item.transition or '').strip().lower()
        eff_transition = raw_t if (raw_t and raw_t != 'cut') else (pl_default_transition or 'cut')
        item_d = {
            'id': item.id,
            'position': item.position,
            'duration': item.duration,
            'effective_duration': playlist_item_duration_seconds(item),
            'type': None,        # 'image' | 'video' | 'webpage'
            'content_url': None,
            'aspect_mode': item.aspect_mode or display.aspect_mode or 'fit',
            'transition': eff_transition,
            'plugin': None,
            'clip_start': item.clip_start,
            'clip_end': item.clip_end,
            'mute_audio': bool(item.mute_audio),
        }
        if item.media:
            m = item.media
            item_d['name'] = m.name
            item_d['type'] = m.media_type
            # Resolve effective audio enable for video items.
            # Precedence (most specific wins):
            #   1. PlaylistItem.mute_audio True   -> mute
            #   2. Playlist.video_audio_default   -> 'on' / 'off' override
            #   3. Media.audio_enabled            -> per-file default
            if m.media_type == 'video':
                pl_default = (playlist.video_audio_default or 'inherit').lower()
                if item.mute_audio:
                    audio_on = False
                elif pl_default == 'on':
                    audio_on = True
                elif pl_default == 'off':
                    audio_on = False
                else:
                    audio_on = bool(getattr(m, 'audio_enabled', True))
                item_d['audio_enabled'] = audio_on
                # Keep mute_audio in sync so legacy clients still work.
                item_d['mute_audio'] = not audio_on
            if m.media_type == 'webpage':
                item_d['content_url'] = m.file_path
            elif m.media_type in ('image', 'video'):
                # Use the stored tenant-scoped path (e.g. 'd1/images/foo.jpg')
                # signed for the unauthenticated player. The legacy
                # 'images/<filename>' URL does NOT include the d<N>/ tenant
                # prefix and 404s on the new tenant-aware /uploads route.
                import storage as _storage
                item_d['content_url'] = (
                    _storage.signed_url(
                        m.file_path, external=False,
                        ttl_seconds=_storage.SIGNED_URL_TTL_PLAYER,
                    )
                    if m.file_path and not m.file_path.startswith(('http://', 'https://'))
                    else m.file_path
                )
            item_d['media'] = {
                'id': m.id,
                'name': m.name,
                'media_type': m.media_type,
                'duration': m.duration,
                'duration_seconds': getattr(m, 'duration_seconds', None),
            }
        elif item.plugin_type:
            item_d['type'] = 'webpage'
            try:
                item_d['content_url'] = build_plugin_url(
                    item.plugin_type, item.plugin_config or {}
                )
            except Exception as e:
                logger.error(f"Plugin URL build failed for {item.plugin_type}: {e}")
                item_d['content_url'] = url_for(
                    'plugins.run_plugin',
                    plugin_type=item.plugin_type
                )
            meta = None
            try:
                meta = get_plugin_meta(item.plugin_type)
            except Exception:
                pass
            item_d['plugin'] = {
                'type': item.plugin_type,
                'key':  (meta or {}).get('key') or item.plugin_type,
                'config': item.plugin_config or {},
                'name': (meta or {}).get('name') or item.plugin_type,
            }
        items_data.append(item_d)

    payload = {
        'playlist_id': playlist.id,
        'playlist_name': playlist.name,
        'schedule_id': chosen.id,
        'schedule_name': chosen.name,
        'default_transition': pl_default_transition or 'cut',
        'random_pool': [s for s in (playlist.random_transitions or '').split(',') if s],
        'version': _resolved_playlist_version(playlist, chosen.id),
        'display_settings': {
            'aspect_mode': display.aspect_mode or 'fit',
            'resolution_x': display.resolution_x,
            'resolution_y': display.resolution_y,
            'orientation': display.orientation or 'landscape',
            'show_media_buttons': _effective_show_media_buttons(display),
            'volume': int(display.volume if display.volume is not None else 100),
            'auto_update_client': bool(getattr(display, 'auto_update_client', False)),
        },
        'items': items_data,
    }

    # Sync block: see sync_playback.py. Adds a ``sync`` key only when
    # this display's group has sync_playback turned on.
    try:
        from sync_playback import build_sync_payload
        sync_block = build_sync_payload(display, items_data, payload['version'])
        if sync_block is not None:
            payload['sync'] = sync_block
    except Exception:
        pass
    return payload


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@player_bp.route('/display/<token>')
def player_page(token):
    """Serve the full-screen browser player page."""
    display = _resolve_display_by_token(token)
    if not display:
        # Return a self-contained page â€” no base.html, no login redirect
        return '''<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"><title>AISignX â€” Invalid Token</title>
<style>
  body{margin:0;background:#0f172a;color:#f1f5f9;font-family:sans-serif;
       display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:16px;}
  h1{color:#f87171;font-size:1.4rem;} p{color:#94a3b8;font-size:.9rem;text-align:center;}
  code{background:#1e293b;padding:2px 8px;border-radius:4px;font-size:.85rem;}
</style></head>
<body>
  <h1>&#10060; Display Token Not Found</h1>
  <p>The token <code>''' + token[:16] + '''â€¦</code> is not registered on this server.</p>
  <p>This display may have been deleted and re-added.<br>
     Re-run setup on the client to get a new token.</p>
</body></html>''', 404

    client_ip = _real_client_ip()

    # Single-client enforcement: check existing SSE connection. Native clients
    # pass a stable client_id; browsers fall back to the IP check until JS starts.
    page_client_id = _client_id_from_request()
    if _token_conflict(token, client_ip, page_client_id):
        _report_duplicate_client_blocked(display, client_ip, page_client_id, 'page')
        return render_template('display_blocked.html',
                               reason="Another client is already connected with this display token."), 409

    # Expire session cache so group_id / schedule changes are visible immediately
    db.session.expire_all()

    # Resolve playlist for bootstrap data (avoids a round-trip on first load)
    playlist_data = _resolve_playlist(display)

    # Update last ping
    display.last_ping = datetime.now()
    display.ip_address = client_ip
    display.status = 'online'
    db.session.commit()

    return render_template(
        'display_player.html',
        display=display,
        token=token,
        playlist_json=json.dumps(playlist_data) if playlist_data else 'null',
        show_media_buttons=_effective_show_media_buttons(display),
        allow_input=bool(display.allow_input),
        show_offline_banner=bool(display.show_offline_banner),
        auto_update_client=bool(getattr(display, 'auto_update_client', False)),
        diagnostics_enabled=bool(getattr(display, 'diagnostics_enabled', False)),
        unlock_pin=str(display.unlock_pin or ''),
        volume=int(display.volume if display.volume is not None else 100),
    )


@player_bp.route('/display/<token>/manifest.webmanifest')
def player_manifest(token):
    """Per-display PWA manifest. Lets Android tablets / Windows Edge install
    `/display/<token>` as a kiosk-style standalone app via "Add to Home
    Screen" -- no Play Store, no APK signing.

    The manifest is per-token so the installed app launches straight into
    the right display. Using `display: fullscreen` and a black theme gives
    a near-kiosk experience on Android. iOS treats `display: standalone`
    as the supported value and ignores the rest gracefully.
    """
    display = _resolve_display_by_token(token)
    if not display:
        return jsonify({'error': 'invalid token'}), 404
    name = (display.name or 'AISignX Display')[:64]
    body = {
        'name':             f'AISignX -- {name}',
        'short_name':       name[:12] or 'Signage',
        'start_url':        f'/display/{token}',
        'scope':            f'/display/{token}',
        'display':          'fullscreen',
        'display_override': ['fullscreen', 'standalone', 'minimal-ui'],
        'orientation':      'landscape',
        'background_color': '#000000',
        'theme_color':      '#000000',
        'icons': [
            {'src': '/static/img/AISignX.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': '/static/img/AISignX.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
        ],
    }
    resp = jsonify(body)
    resp.headers['Content-Type'] = 'application/manifest+json'
    resp.headers['Cache-Control'] = 'no-cache'
    return resp


@player_bp.route('/display/<token>/events')
def player_events(token):
    """SSE stream â€” sends heartbeats and 'reload' events to the player."""
    display = _resolve_display_by_token(token)
    if not display:
        abort(404)

    client_ip = _real_client_ip()

    client_id = _client_id_from_request()

    # Single-client enforcement
    if _token_conflict(token, client_ip, client_id):
        _report_duplicate_client_blocked(display, client_ip, client_id, 'events')
        return jsonify({'error': 'conflict'}), 409

    # Resolve initial playlist version
    with display_player_app_context():
        playlist_data = _resolve_playlist(display)
    current_version = playlist_data['version'] if playlist_data else ''

    _register_connection(token, client_ip, current_version, client_id)
    logger.info(f"SSE connection opened: display={display.name} ip={client_ip}")

    # Snapshot of display settings so we can detect changes
    current_settings_ver = json.dumps({
        'allow_input': bool(display.allow_input),
        'show_media_buttons': _effective_show_media_buttons(display),
        'show_offline_banner': bool(display.show_offline_banner),
        'auto_update_client': bool(getattr(display, 'auto_update_client', False)),
        'sync_playback_opt_out': bool(getattr(display, 'sync_playback_opt_out', False)),
        'diagnostics_enabled': bool(getattr(display, 'diagnostics_enabled', False)),
        'unlock_pin': str(display.unlock_pin or ''),
        'volume': int(display.volume if display.volume is not None else 100),
    }, sort_keys=True)

    # Track last emergency broadcast state sent to this display
    current_emergency_id = None

    def generate():
        nonlocal current_version, current_settings_ver, current_emergency_id
        last_heartbeat = time.time()

        # â”€â”€ On connect: push current settings AND playlist so the client
        #    is fully synced â€” recovers plugins after server outage,
        #    and applies setting toggles even if version hasn't changed.
        try:
            disp_boot = _resolve_display_by_token(token)
            if disp_boot:
                # Send current settings
                boot_settings = {
                    'allow_input': bool(disp_boot.allow_input),
                    'show_media_buttons': _effective_show_media_buttons(disp_boot),
                    'show_offline_banner': bool(disp_boot.show_offline_banner),
                    'auto_update_client': bool(getattr(disp_boot, 'auto_update_client', False)),
                    'sync_playback_opt_out': bool(getattr(disp_boot, 'sync_playback_opt_out', False)),
                    'diagnostics_enabled': bool(getattr(disp_boot, 'diagnostics_enabled', False)),
                    'unlock_pin': str(disp_boot.unlock_pin or ''),
                    'volume': int(disp_boot.volume if disp_boot.volume is not None else 100),
                }
                yield f'event: settings\ndata: {json.dumps(boot_settings)}\n\n'

                # Send current playlist (forces plugin iframes to re-fetch
                # after server downtime even if the playlist itself didn't change)
                if playlist_data:
                    payload = json.dumps({
                        'version': current_version,
                        'playlist': playlist_data,
                    })
                    yield f'event: reload\ndata: {payload}\n\n'

                # Send any active emergency
                boot_emergency = next(
                    (b for b in EmergencyBroadcast.query.filter_by(
                        is_active=True, domain_id=disp_boot.domain_id).all()
                     if b.is_live() and b.applies_to(disp_boot)),
                    None
                )
                if boot_emergency:
                    current_emergency_id = boot_emergency.id
                    yield f'event: emergency\ndata: {json.dumps(boot_emergency.to_dict())}\n\n'
                    logger.info(f"SSE emergency pushed on connect to display={disp_boot.name} id={boot_emergency.id}")
        except Exception as e:
            logger.error(f"SSE boot push error for {token}: {e}")

        try:
            while True:
                time.sleep(1)  # Poll interval (push_playlist_reload handles urgent updates)

                # Drain any immediately-pushed events (emergency, clear, etc.)
                for queued_event in _drain_push_queue(token):
                    yield queued_event

                # Heartbeat
                if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
                    yield ': heartbeat\n\n'
                    last_heartbeat = time.time()
                    _touch_connection(token)

                # Check if this connection was superseded
                if not _is_connection_alive(token):
                    yield 'event: disconnect\ndata: {}\n\n'
                    break
                if _connection_ip(token) != client_ip:
                    yield 'event: disconnect\ndata: {"reason":"superseded"}\n\n'
                    break
                active_client_id = _connection_client_id(token)
                if active_client_id and client_id and active_client_id != client_id:
                    yield 'event: disconnect\ndata: {"reason":"superseded"}\n\n'
                    break

                try:
                    db.session.expire_all()   # bust SQLAlchemy cache so group_id / settings changes are visible
                    disp = _resolve_display_by_token(token)
                    if not disp:
                        break

                    # Fallback poll: detect emergency state changes not caught by push
                    active_broadcasts = EmergencyBroadcast.query.filter_by(
                        is_active=True, domain_id=disp.domain_id).all()
                    active_emergency = next(
                        (b for b in active_broadcasts if b.is_live() and b.applies_to(disp)),
                        None
                    )
                    new_emergency_id = active_emergency.id if active_emergency else None
                    if new_emergency_id != current_emergency_id:
                        current_emergency_id = new_emergency_id
                        if active_emergency:
                            yield f'event: emergency\ndata: {json.dumps(active_emergency.to_dict())}\n\n'
                            logger.info(f"SSE emergency pushed to display={disp.name} id={active_emergency.id}")
                        else:
                            yield 'event: emergency_clear\ndata: {}\n\n'
                            logger.info(f"SSE emergency cleared for display={disp.name}")

                    # Push updated display settings if they changed
                    new_settings = {
                        'allow_input': bool(disp.allow_input),
                        'show_media_buttons': _effective_show_media_buttons(disp),
                        'show_offline_banner': bool(disp.show_offline_banner),
                        'auto_update_client': bool(getattr(disp, 'auto_update_client', False)),
                        'sync_playback_opt_out': bool(getattr(disp, 'sync_playback_opt_out', False)),
                        'diagnostics_enabled': bool(getattr(disp, 'diagnostics_enabled', False)),
                        'unlock_pin': str(disp.unlock_pin or ''),
                        'volume': int(disp.volume if disp.volume is not None else 100),
                    }
                    new_settings_ver = json.dumps(new_settings, sort_keys=True)
                    if new_settings_ver != current_settings_ver:
                        current_settings_ver = new_settings_ver
                        yield f'event: settings\ndata: {json.dumps(new_settings)}\n\n'
                        logger.info(f"SSE settings pushed to display={disp.name}")

                    new_data = _resolve_playlist(disp)
                    new_version = new_data['version'] if new_data else ''
                    if new_version != current_version:
                        current_version = new_version
                        _touch_connection(token, new_version)
                        payload = json.dumps({
                            'version': new_version,
                            'playlist': new_data,
                        })
                        yield f'event: reload\ndata: {payload}\n\n'
                        logger.info(f"SSE reload pushed to display={disp.name} version={new_version or '(empty)'}")
                except Exception as e:
                    logger.error(f"SSE version check error for {token}: {e}")

        except GeneratorExit:
            pass
        finally:
            _unregister_connection(token)
            logger.info(f"SSE connection closed: display={display.name} ip={client_ip}")

    response = Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',   # disable nginx buffering
            'Connection': 'keep-alive',
        }
    )
    return response


@player_bp.route('/display/<token>/ping', methods=['POST'])
def player_ping(token):
    """Keepalive ping from the browser player."""
    display = _resolve_display_by_token(token)
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid token'}), 404

    client_ip = _real_client_ip()
    data = request.get_json(silent=True) or {}
    client_id = _client_id_from_request(data)

    # Single-client enforcement
    if _token_conflict(token, client_ip, client_id):
        _report_duplicate_client_blocked(display, client_ip, client_id, 'ping')
        return jsonify({'status': 'error', 'message': 'Another client is already connected.'}), 409

    _touch_connection(token)
    # Hot path: batched heartbeat instead of an immediate DB commit. The
    # periodic 'heartbeat-flush' job aggregates these and writes once per
    # minute. See heartbeat.py for the full rationale.
    heartbeat.record(token, ip=client_ip, status='online',
                     current_content=data.get('current_content'))
    # If the client reported an app version (Electron/native shell), keep
    # the SSE connection record in sync so admins can see post-update
    # rollouts on the displays page without waiting for a full reconnect.
    cv = data.get('app_version') or data.get('version')
    if cv:
        _touch_connection(token, version=str(cv)[:32])
        # Persist a changed version to the DB so it survives restarts and
        # shows up in /api/displays. Cheap: only writes when it actually
        # changed.
        if (display.app_version or '') != str(cv)[:20]:
            display.app_version = str(cv)[:20]
            db.session.commit()
    return jsonify({'status': 'ok'})


@player_bp.route('/api/display/<token>/playlist', methods=['GET'])
def player_api_playlist(token):
    """
    JSON playlist endpoint for the browser player.
    Token is in the URL (not the Authorization header).
    """
    display = _resolve_display_by_token(token)
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid token'}), 404

    client_ip = _real_client_ip()
    client_id = _client_id_from_request()

    if _token_conflict(token, client_ip, client_id):
        _report_duplicate_client_blocked(display, client_ip, client_id, 'playlist')
        return jsonify({'status': 'error', 'message': 'Another client is already connected.'}), 409

    data = _resolve_playlist(display)
    if not data:
        # Expire and retry once â€” catches stale SQLAlchemy session cache
        db.session.expire_all()
        display = _resolve_display_by_token(token)
        data = _resolve_playlist(display) if display else None
    if not data:
        return jsonify({'status': 'error', 'message': 'No active schedule or playlist for this display.'}), 404

    _touch_connection(token, data['version'])
    # Batched heartbeat (see player_ping for rationale).
    heartbeat.record(token, ip=client_ip, status='online')

    return jsonify({'status': 'success', 'playlist': data})


# ---------------------------------------------------------------------------
# Context helper â€” SSE generator runs in the request context so we need
# to push an app context for DB queries inside the generator thread.
# ---------------------------------------------------------------------------
def display_player_app_context():
    """No-op context manager â€” DB queries in the generator already have the
    request/app context because stream_with_context handles that."""
    from contextlib import contextmanager
    @contextmanager
    def _noop():
        yield
    return _noop()


# ---------------------------------------------------------------------------
# Synchronized playback - clock skew calibration
#
# Returns the server's wall-clock so display players can compute their
# local clock offset. Synced playback uses the offset to convert the
# server-provided anchor (unix-ms) into a local-clock target.
#
# Token-gated only as a sanity check -- the response carries no secrets,
# but we keep it inside the per-display URL space so casual scrapers
# can't enumerate it.
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Client-side diagnostic logging.
#
#   POST /api/display/<token>/diagnostics
#     Body: {"entries": [{"level": "...", "source": "...",
#                          "message": "...", "client_ts": ISO8601,
#                          "meta": {...}}, ...]}
#
# Off by default; only accepted when Display.diagnostics_enabled is True
# (admins opt-in per display). Hard caps keep the table from growing
# without bound:
#   * MAX_ENTRIES_PER_BATCH  -- stop a runaway client flooding us
#   * MAX_ROWS_PER_DISPLAY   -- prune oldest beyond this on each batch
#   * MAX_MESSAGE_LEN        -- truncate very long messages
# ---------------------------------------------------------------------------
_DIAG_MAX_ENTRIES_PER_BATCH = 200
_DIAG_MAX_ROWS_PER_DISPLAY = 5000
_DIAG_MAX_MESSAGE_LEN = 4000
_DIAG_VALID_LEVELS = {'info', 'warn', 'error', 'sync', 'net', 'play', 'debug'}


def _parse_client_ts(raw):
    if not raw:
        return None
    try:
        s = str(raw).replace('Z', '+00:00')
        dt = datetime.fromisoformat(s)
        try:
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except Exception:
            return dt
    except Exception:
        return None


@player_bp.route('/api/display/<token>/diagnostics', methods=['POST'])
def player_api_diagnostics_ingest(token):
    display = _resolve_display_by_token(token)
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid token'}), 404
    if not bool(getattr(display, 'diagnostics_enabled', False)):
        # Tell the client to stop. The player flips its local flag off on 423.
        return jsonify({'status': 'disabled'}), 423
    data = request.get_json(silent=True) or {}
    entries = data.get('entries') or []
    if not isinstance(entries, list):
        return jsonify({'status': 'error', 'message': 'entries must be a list'}), 400
    if len(entries) > _DIAG_MAX_ENTRIES_PER_BATCH:
        entries = entries[-_DIAG_MAX_ENTRIES_PER_BATCH:]
    stored = 0
    for e in entries:
        if not isinstance(e, dict):
            continue
        level = (e.get('level') or 'info').strip().lower()[:16]
        if level not in _DIAG_VALID_LEVELS:
            level = 'info'
        source = (e.get('source') or '').strip()[:64] or None
        msg = e.get('message')
        if msg is None:
            msg = ''
        elif not isinstance(msg, str):
            try:
                msg = json.dumps(msg)[:_DIAG_MAX_MESSAGE_LEN]
            except Exception:
                msg = str(msg)[:_DIAG_MAX_MESSAGE_LEN]
        else:
            msg = msg[:_DIAG_MAX_MESSAGE_LEN]
        meta = e.get('meta')
        if meta is not None and not isinstance(meta, (dict, list)):
            meta = {'value': meta}
        row = DisplayDiagnostic(
            display_id=display.id,
            client_ts=_parse_client_ts(e.get('client_ts')),
            level=level,
            source=source,
            message=msg,
            meta=meta,
        )
        db.session.add(row)
        stored += 1
    db.session.commit()
    # Per-display prune: keep most recent _DIAG_MAX_ROWS_PER_DISPLAY.
    try:
        total = db.session.query(DisplayDiagnostic.id).filter_by(display_id=display.id).count()
        excess = total - _DIAG_MAX_ROWS_PER_DISPLAY
        if excess > 0:
            # SQLite has no LIMIT in DELETE; do a subquery.
            old_ids = [r[0] for r in
                       db.session.query(DisplayDiagnostic.id)
                                  .filter_by(display_id=display.id)
                                  .order_by(DisplayDiagnostic.received_at.asc())
                                  .limit(excess).all()]
            if old_ids:
                db.session.query(DisplayDiagnostic).filter(
                    DisplayDiagnostic.id.in_(old_ids)).delete(synchronize_session=False)
                db.session.commit()
    except Exception as e:
        logger.warning(f'diagnostics prune failed for display={display.id}: {e}')
    return jsonify({'status': 'success', 'stored': stored})


@player_bp.route('/api/display/<token>/server_time', methods=['GET'])
def player_api_server_time(token):
    display = _resolve_display_by_token(token)
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid token'}), 404
    return jsonify({
        'status':       'success',
        'server_now_ms': int(time.time() * 1000),
    })


# ---------------------------------------------------------------------------
# Per-display capability negotiation - Phase 4
#
#   POST /api/display/<token>/capabilities
#       Body: {"max_video_height": 1080, "codecs": ["h264"], ...}
#
# Idempotent. The display calls this on first connect and after any
# environment change (resolution swap, codec install, etc). Audited
# only when the snapshot actually changed -- otherwise repeated
# heartbeat-style reports would flood the log.
# ---------------------------------------------------------------------------
@player_bp.route('/api/display/<token>/capabilities', methods=['POST'])
def player_api_capabilities(token):
    display = _resolve_display_by_token(token)
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid token'}), 404

    payload = request.get_json(silent=True) or {}
    from capabilities import normalize_capabilities
    normalized = normalize_capabilities(payload)

    old = display.capabilities or {}
    if normalized != old:
        display.capabilities = normalized
        db.session.commit()
        try:
            from audit import audit
            audit('display.capabilities_update',
                   target_type='display', target_id=str(display.id),
                   payload={'from': old, 'to': normalized})
        except Exception:
            pass

    return jsonify({
        'status':       'success',
        'capabilities': display.capabilities or {},
        'changed':      normalized != old,
    })


# ---------------------------------------------------------------------------
# Proof of Play - Phase 4 (optional)
#
#   POST /api/display/<token>/proof-of-play
#       Body: {
#         "events": [
#           {"item_type": "image", "media_id": 12, "playlist_id": 3,
#            "item_name": "promo.png", "duration_ms": 8000,
#            "completed": true,
#            "started_at": "2025-01-01T12:34:56Z"}, ...
#         ]
#       }
#
# Token-gated only. Bulk-friendly so offline players can drain a backlog
# in one request when they reconnect. Off by default at the server level
# (see settings.proof_of_play.enabled). Returns the count actually accepted
# so a client knows how much of its backlog was persisted.
# ---------------------------------------------------------------------------
@player_bp.route('/api/display/<token>/proof-of-play', methods=['POST'])
def player_api_proof_of_play(token):
    display = _resolve_display_by_token(token)
    if not display:
        return jsonify({'status': 'error', 'message': 'Invalid token'}), 404

    import proof_of_play as pop
    if not pop.is_enabled():
        # Tell the client to stop sending until re-enabled. Client can re-probe
        # with the next playlist refresh.
        return jsonify({'status': 'disabled', 'accepted': 0}), 200

    payload = request.get_json(silent=True) or {}
    events  = payload.get('events') or []
    if not isinstance(events, list):
        return jsonify({'status': 'error', 'message': 'events must be a list'}), 400

    accepted = 0
    from datetime import datetime as _dt
    for ev in events[:500]:                  # bound per-request work
        if not isinstance(ev, dict):
            continue
        started = None
        raw = ev.get('started_at')
        if raw:
            try:
                # Accept ISO-8601 with or without trailing Z
                started = _dt.fromisoformat(str(raw).replace('Z', '+00:00')).replace(tzinfo=None)
            except (TypeError, ValueError):
                started = None
        ok = pop.record(
            display     = display,
            item_type   = ev.get('item_type'),
            item_name   = ev.get('item_name'),
            media_id    = ev.get('media_id'),
            playlist_id = ev.get('playlist_id'),
            plugin_key  = ev.get('plugin_key'),
            duration_ms = ev.get('duration_ms'),
            completed   = ev.get('completed', True),
            started_at  = started,
        )
        if ok:
            accepted += 1

    return jsonify({'status': 'ok', 'accepted': accepted})
