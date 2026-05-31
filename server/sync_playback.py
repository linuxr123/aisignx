"""
Synchronized playback - Phase 4.

Wall-clock anchored sync: every display in a group with
``sync_playback=True`` derives the current slide index and elapsed
time from a shared anchor timestamp + the deterministic duration sum
of the playlist items. No continuous coordinator chatter is needed --
once the displays know the anchor and the duration table, they stay in
lockstep on their own.

Anchor stability
----------------
The anchor is the unix-ms timestamp at which "slide 0" was conceptually
started for a (group_id, playlist_version) pair. It is cached in
``system_settings`` under the key ``sync_anchor.<gid>.<version>`` so all
displays in the group see the same value across sessions and reboots.
A new playlist version produces a new key -- old anchors quietly age
out of relevance.

Why wall-clock instead of server-pushed "now show slide 3"
----------------------------------------------------------
Server-pushed sync degrades badly under network jitter: a 200ms delay
between two displays receiving the push is 200ms of visible drift.
Wall-clock sync only drifts by the displays' internal clock skew (and
we measure & compensate that with a single ``/api/server_time`` call
on connect). It also survives SSE reconnects and brief offline windows
without issuing a single extra request.

Limitations (documented in MULTI_TENANCY.md)
--------------------------------------------
* Durations must be deterministic. Synced playlists ignore the plugin
  ``signage:complete`` early-advance signal and the video ``ended``
  event -- they advance strictly on ``item.duration`` so all displays
  hold a consistent index.
* Video items with duration 0 use detected media length, or clip_start to
  clip_end when a clip end is set. Non-video items missing a duration get a
  10s default.
* When sync is on, the server applies a group-common capability filter:
  any item incompatible with a known member is dropped for the whole
  group so every display keeps the same slide sequence.
"""
import time
from typing import List, Dict, Any, Optional

from logging_config import logger
from media_duration import item_dict_duration_ms


_ANCHOR_KEY_FMT = 'sync_anchor.{gid}.{version}'

# When a new playlist version gets its first anchor, start the shared cycle
# slightly in the future so every display in the group can prefetch media
# before slide 0 begins (reduces visible desync after schedule changes).
SYNC_ANCHOR_GRACE_MS = 10_000


def group_sync_playback_active(display) -> bool:
    """True when this display's assigned group has wall-clock sync enabled.

    Used by playlist resolution to skip per-display capability filtering: if
    each player dropped a different subset of slides, they would receive
    different ``item_durations_ms`` tables while sharing one anchor — indices
    would never line up.
    """
    if getattr(display, 'sync_playback_opt_out', False):
        return False
    if not getattr(display, 'group_id', None):
        return False
    try:
        from models import DisplayGroup
        from tenant_filter import bypass_tenant_filter
        with bypass_tenant_filter():
            g = DisplayGroup.query.get(display.group_id)
        return bool(g and getattr(g, 'sync_playback', False))
    except Exception:
        return False


# Default per-item duration when an item ships with duration <= 0.
# Matches the existing unsynced fallback in display_player.js.
_DEFAULT_ITEM_DUR_S = 10


def _server_now_ms() -> int:
    """Server wall-clock in unix-ms. Single helper so test suites can
    patch it if needed."""
    return int(time.time() * 1000)


def _item_duration_ms(item: Dict[str, Any]) -> int:
    """Authoritative per-item duration for sync purposes."""
    return item_dict_duration_ms(item, default=_DEFAULT_ITEM_DUR_S)


def cycle_total_ms(items: List[Dict[str, Any]]) -> int:
    """Sum of all per-item durations. Returns 0 for an empty list (the
    caller should disable sync in that case)."""
    return sum(_item_duration_ms(it) for it in items)


def get_or_create_anchor(group_id: int, playlist_version: str,
                          now_ms: Optional[int] = None) -> int:
    """Return the cached anchor for (group_id, playlist_version), or
    create one at `now_ms` (defaults to the current server clock).

    Storage is ``system_settings`` keyed on the version, so a new
    playlist version transparently produces a fresh anchor without us
    having to garbage-collect the old keys -- they just become unread.
    """
    import settings as _settings
    key = _ANCHOR_KEY_FMT.format(gid=int(group_id), version=playlist_version)
    cached = _settings.get(key)
    if cached:
        try:
            return int(cached)
        except (TypeError, ValueError):
            logger.warning(f'sync: bad cached anchor {cached!r} for {key}, regenerating')
    now = now_ms if now_ms is not None else _server_now_ms()
    anchor = int(now) + SYNC_ANCHOR_GRACE_MS
    _settings.set(key, str(anchor), _allow_unknown=True)
    logger.info(f'sync: anchor created for group={group_id} version={playlist_version} '
                f'at {anchor}ms (grace {SYNC_ANCHOR_GRACE_MS}ms)')
    return anchor


def build_sync_payload(display, items: List[Dict[str, Any]],
                        playlist_version: str) -> Optional[Dict[str, Any]]:
    """Compute the ``sync`` block for a playlist response, or return
    None if sync isn't applicable for this display.

    Applicable when:
      * The display is in a group, AND
      * the group has ``sync_playback=True``, AND
      * the playlist has at least one item with a non-zero duration sum.
    """
    if getattr(display, 'sync_playback_opt_out', False):
        return None
    if not getattr(display, 'group_id', None):
        return None
    try:
        from models import DisplayGroup
        from tenant_filter import bypass_tenant_filter
        with bypass_tenant_filter():    # tenant-ok: explicit FK lookup keyed on display's own group_id
            group = DisplayGroup.query.get(display.group_id)
    except Exception:
        return None
    if not group or not getattr(group, 'sync_playback', False):
        return None
    total = cycle_total_ms(items)
    if total <= 0:
        return None
    anchor = get_or_create_anchor(group.id, playlist_version)
    # Per-item table so the client can map elapsed-ms to a slide index
    # without re-deriving the duration logic. Index alignment matches
    # the playlist's `items` order.
    table = [_item_duration_ms(it) for it in items]
    return {
        'enabled':         True,
        'group_id':        group.id,
        'anchor_unix_ms':  anchor,
        'cycle_total_ms':  total,
        'item_durations_ms': table,
        'server_now_ms':   _server_now_ms(),
    }
