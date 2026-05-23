"""
Per-display capability negotiation - Phase 4.

Display clients self-report their hardware/software capabilities on
connect. The server filters playlist items the display can't render
and picks the best media variant for the screen.

Capability shape (all fields optional; missing = "no information"):

    {
      "max_video_height":  1080,           # int, hard ceiling for video variants
      "max_image_dim":     4096,           # int, hard ceiling for image variants
      "screen_w":          1920,           # int, current screen width
      "screen_h":          1080,           # int, current screen height
      "codecs":            ["h264", "vp9"],# list[str], video codecs the player supports
      "audio":             true,           # bool, has working audio output
      "browser":           "Chrome/126",   # str, free-form UA hint
    }

Filtering rules
---------------
* Image / webpage / plugin items: always allowed (never the long pole).
* Video items: if the media's required codec is declared and the
  display's `codecs` list is also declared and doesn't include it, the
  item is skipped. Missing data on either side is treated as "compatible"
  -- we don't want to over-filter on incomplete capability reports.
* Plugin items: if the plugin declares a sandbox token in
  `permissions` that the player can't honor (rare today; reserved for
  future capabilities), the item is skipped. Currently a no-op
  placeholder so the policy plumbing is in one spot when we need it.

Why "no info => allowed"
------------------------
Capability reports are best-effort. A first-boot client may not have
sent its capabilities yet; a legacy client may never send them. Making
"unknown" mean "denied" would cause every display in the field to go
blank on the first connect after this code ships -- catastrophic for an
operator-visible Phase-4 feature. "Unknown means allowed" preserves
back-compat at the cost of letting a display fail silently if its
operator never set up capability reporting.
"""
from typing import Dict, List, Tuple, Any, Optional

from logging_config import logger


# Known media-type -> required codec hint key on the Media model. Today
# the schema doesn't store an explicit codec field, so we infer from
# variants/file extension. This map is deliberately tiny -- expand when
# the media pipeline starts annotating files with codec metadata.
_VIDEO_EXT_TO_CODEC = {
    'mp4':  'h264',
    'm4v':  'h264',
    'mov':  'h264',
    'webm': 'vp9',
    'mkv':  'h264',
}


def _video_codec_hint(media) -> Optional[str]:
    """Best-effort codec inference. Returns None if we can't tell --
    treat that as "compatible with anything" upstream."""
    if media is None or not getattr(media, 'file_path', None):
        return None
    # Prefer an explicit hint in variants if it exists (future-proof).
    v = getattr(media, 'variants', None) or {}
    for vv in v.values():
        if isinstance(vv, dict) and vv.get('codec'):
            return str(vv['codec']).lower()
    path = (media.file_path or '').lower()
    if '.' in path:
        ext = path.rsplit('.', 1)[-1].split('?', 1)[0]
        return _VIDEO_EXT_TO_CODEC.get(ext)
    return None


def display_can_render(item, display) -> Tuple[bool, str]:
    """Return (can_render, reason). reason is a short string only used
    for the "skipped item" log line; safe to ignore on the happy path.

    Conservative by design: missing capability data => allowed. See the
    module docstring for the rationale.
    """
    caps = (getattr(display, 'capabilities', None) or {}) if display else {}
    media = getattr(item, 'media', None)

    # Plugin items are always allowed at the resolver level. Per-tenant
    # policy (plugin_system.resolve_plugin_policy) gates them earlier
    # and the iframe sandbox enforces capabilities at the browser level.
    if getattr(item, 'plugin_type', None):
        return True, ''

    if media is None:
        return True, ''   # broken item; downstream code handles

    mtype = getattr(media, 'media_type', None)

    if mtype == 'video':
        # Codec compatibility check. Only filter if BOTH the media's
        # codec is known and the display's codec list is known.
        media_codec = _video_codec_hint(media)
        display_codecs = caps.get('codecs')
        if media_codec and isinstance(display_codecs, list) and display_codecs:
            normalized = [str(c).lower() for c in display_codecs]
            if media_codec not in normalized:
                return False, f'video codec {media_codec!r} not in {normalized}'

    # Image / webpage / unknown: always allowed. Variant selection
    # (which honors max_image_dim / max_video_height) is handled
    # separately when content_url is built.
    return True, ''


def filter_items_for_display(items: List[Any], display
                              ) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Partition `items` into (kept, skipped). `skipped` is a list of
    diagnostic dicts so the playlist response can surface
    `excluded_items` for operator visibility."""
    kept, skipped = [], []
    for it in items:
        ok, reason = display_can_render(it, display)
        if ok:
            kept.append(it)
        else:
            entry = {
                'item_id':   getattr(it, 'id', None),
                'media_id':  getattr(getattr(it, 'media', None), 'id', None),
                'name':      getattr(getattr(it, 'media', None), 'name', None) or 'unknown',
                'reason':    reason,
            }
            skipped.append(entry)
            try:
                logger.info(
                    f'capabilities: skipping item {entry["item_id"]} for '
                    f'display id={getattr(display, "id", "?")}: {reason}'
                )
            except Exception:
                pass
    return kept, skipped


def filter_items_for_sync_group(items: List[Any], display
                                ) -> Tuple[List[Any], List[Dict[str, Any]]]:
    """Filter to a group-common compatible set for synchronized playback.

    Every display in a sync group must see the exact same item sequence.
    We therefore keep only items renderable by every group member with
    known incompatibility. Unknown capabilities remain permissive.
    """
    from models import Display
    group_id = getattr(display, 'group_id', None)
    if not group_id:
        return filter_items_for_display(items, display)
    group_members = Display.query.filter_by(group_id=group_id).all()
    if not group_members:
        group_members = [display]

    kept, skipped = [], []
    for it in items:
        bad = []
        for member in group_members:
            ok, reason = display_can_render(it, member)
            if not ok:
                bad.append((member, reason))
        if not bad:
            kept.append(it)
            continue
        offenders = [str(getattr(m, 'id', '?')) for m, _ in bad]
        reason = '; '.join(
            f"display {getattr(m, 'id', '?')}: {r}" for m, r in bad
        )
        entry = {
            'item_id':   getattr(it, 'id', None),
            'media_id':  getattr(getattr(it, 'media', None), 'id', None),
            'name':      getattr(getattr(it, 'media', None), 'name', None) or 'unknown',
            'reason':    f'incompatible with sync-group member(s): {",".join(offenders)}',
            'detail':    reason,
        }
        skipped.append(entry)
        try:
            logger.info(
                'capabilities(sync): skipping item %s for group %s (%s)',
                entry['item_id'], group_id, reason
            )
        except Exception:
            pass
    return kept, skipped


def normalize_capabilities(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate / coerce a client-reported capabilities dict. Drops
    unknown keys (forward-compat: if a client adds new keys we just
    pass them through), coerces ints, lower-cases codec strings.

    Returns a fresh dict; never mutates the input.
    """
    if not isinstance(payload, dict):
        return {}
    out = {}
    for k, v in payload.items():
        if k in ('max_video_height', 'max_image_dim', 'screen_w', 'screen_h'):
            try:
                out[k] = int(v)
            except (TypeError, ValueError):
                pass    # silently drop bad values
        elif k == 'codecs' and isinstance(v, list):
            out[k] = sorted({str(c).strip().lower() for c in v if c})
        elif k == 'audio':
            out[k] = bool(v)
        elif isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        # Lists/dicts of unknown shape: passthrough only if JSON-safe
        elif isinstance(v, (list, dict)):
            try:
                import json as _json
                _json.dumps(v)
                out[k] = v
            except (TypeError, ValueError):
                pass
    return out
