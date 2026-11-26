"""What a screen should play: the manifest the player API, SSE hello and heartbeat share.

Fields may only ever be added; clients ignore keys they don't know. Phase 4 added
`source` (why this playlist plays) and `emergency` (the overlay to show, or null).
Display options added `settings` (orientation, volume, offline banner, input, unlock
hash; see displays/options.py); a display's fit override replaces every item's `fit`.
Player controls added `settings.kiosk_lock` (lock the native apps) and
`settings.resume_after_s` (auto-resume after a pause).
Plugins added items of type "plugin" with a `plugin` block (see plugins/service.py and
docs/player-api.md); older players drop types they do not know.

`fit` keeps the domain vocabulary; the browser player maps it to CSS object-fit:
    fit -> contain, fill -> cover, stretch -> fill
"""
import hashlib
import json
import time
from datetime import datetime

from flask import current_app, url_for

from ..models import Display, Playlist
from ..models.media import MEDIA_IMAGE, MEDIA_PLUGIN, MEDIA_VIDEO, MEDIA_WEB
from ..models.playlist import FIT_MODES

MANIFEST_SCHEMA = 1
_FILE_TYPES = (MEDIA_IMAGE, MEDIA_VIDEO)


def resolve_playlist(display: Display, now: datetime) -> Playlist | None:
    """The playlist a display should show right now (emergency, schedule or default)."""
    from ..schedules.resolve import resolve

    return resolve(display, now).playlist


def is_playable(item) -> bool:
    """Whether a playlist item can appear in a manifest. No url_for, so the resolver
    can use it outside a request."""
    media = item.media
    if media is None or getattr(item, "enabled", True) is False:
        return False
    if media.media_type in _FILE_TYPES:
        return bool(media.stored_name)
    if media.media_type == MEDIA_WEB:
        if media.shows_snapshot:  # a page that cannot be embedded plays as its screenshot
            return bool(media.snapshot_name)
        return (media.url or "").strip().lower().startswith(("http://", "https://"))
    if media.media_type == MEDIA_PLUGIN:
        from ..plugins.service import is_playable_plugin

        return is_playable_plugin(media)
    return False


def _file_entry(media) -> dict:
    """The file fields of an uploaded image or video (also used inside plugin blocks)."""
    return {
        "url": url_for("player_api.media_file", media_id=media.id, name=media.stored_name),
        "mime": media.mime_type,
        "sha256": media.checksum_sha256,
        "size": media.file_size,
        "width": media.width,
        "height": media.height,
    }


file_entry = _file_entry  # public name for plugin hooks (manifest_extra)


def _volume(item) -> int:
    """The item's share of the display volume, 0-100."""
    v = getattr(item, "volume", None)
    return 100 if v is None else max(0, min(100, int(v)))


def _snapshot_entry(media) -> dict:
    """The file fields of a web page's screenshot (media/webpage.py), played as an image."""
    from ..media.webpage import SNAPSHOT_SIZE

    return {
        "url": url_for("player_api.media_file", media_id=media.id, name=media.snapshot_name),
        "mime": "image/png",
        "sha256": media.snapshot_sha256,
        "size": media.snapshot_size,
        "width": SNAPSHOT_SIZE[0],
        "height": SNAPSHOT_SIZE[1],
    }


def _video_facts(media) -> dict | None:
    """codec string, size, frame rate and bitrate of a video, so the player can ask its browser
    whether this screen decodes it smoothly (device.js mediaSupport). None when unknown."""
    from ..media import compat

    codec = compat.codec_string(media.video_info)
    if not codec or not media.width or not media.height:
        return None
    info = media.video_info
    return {"codec": codec, "width": media.width, "height": media.height,
            "fps": info.get("fps") or 25, "bitrate": info.get("bitrate") or 0}


def _clip_ms(plugin_block: dict) -> int | None:
    """Length in ms of a single YouTube video set to play from a start time to an end time."""
    embed = plugin_block.get("embed") or {}
    ids = embed.get("video_ids")
    start, end = embed.get("start") or 0, embed.get("end") or 0
    if embed.get("list") or embed.get("live") or not isinstance(ids, list) or len(ids) != 1:
        return None
    if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start >= 0:
        return int(round((end - start) * 1000))
    return None


def _item_entry(item, fit: str | None = None, synced: bool = False) -> dict | None:
    """One manifest item. fit: the display's override for every item, if any. synced: the display's
    group plays in sync (slides run for their set duration; see build_manifest)."""
    if not is_playable(item):
        return None
    media = item.media
    is_video = media.media_type == MEDIA_VIDEO
    plugin = None
    shown_type = media.media_type
    if media.media_type in _FILE_TYPES:
        entry = _file_entry(media)
    elif media.media_type == MEDIA_PLUGIN:
        from ..plugins.service import manifest_fields

        plugin = manifest_fields(media, muted=item.mute, synced=synced)
        if plugin is None:
            return None
        entry = {"url": plugin["url"], "mime": None, "sha256": None, "size": None,
                 "width": None, "height": None}
    elif media.shows_snapshot:  # a web page shown as a screenshot: an image to the player
        entry = _snapshot_entry(media)
        shown_type = MEDIA_IMAGE
    else:  # web page
        entry = {"url": media.url.strip(), "mime": None, "sha256": None, "size": None,
                 "width": None, "height": None}

    media_ms = None
    if is_video and media.duration_seconds:
        media_ms = round(media.duration_seconds * 1000)
    video = _video_facts(media) if is_video else None
    result = {
        "id": item.id,
        "media_id": media.id,
        "type": shown_type,
        **entry,
        "duration_ms": item.effective_duration * 1000,
        "media_duration_ms": media_ms,
        "transition": item.effective_transition,  # may be "random"
        "fit": fit or item.fit_mode,
        "muted": bool(item.mute) if is_video else True,
        "volume": _volume(item),
    }
    if video:
        result["video"] = video
    if plugin is not None:
        result["muted"] = plugin["muted"]
        result["plugin"] = plugin["plugin"]
        clip_ms = _clip_ms(plugin["plugin"]) if synced else None
        if clip_ms:
            # Synced screens run every slide for its set duration. A YouTube clip with a start and an
            # end time lasts exactly that long, so the slide does too (otherwise it would loop).
            result["duration_ms"] = clip_ms
    return result


def _emergency_entry(res) -> dict | None:
    em = res.emergency
    if em is None:
        return None
    from ..schedules.resolve import layout_for

    return em.to_player(layout_for(em))


def manifest_version(body: dict) -> str:
    """Content hash of a manifest body, ignoring version and server_time_ms."""
    stable = {k: v for k, v in body.items() if k not in ("version", "server_time_ms")}
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_manifest(display: Display, now: datetime | None = None, ctx=None) -> dict:
    """Build the manifest for one display. Needs a request context (for url_for).

    ctx: an optional resolve.Context shared across several displays.
    """
    from ..displays.options import settings_payload
    from ..schedules.resolve import resolve  # resolve imports is_playable from here

    res = resolve(display, now, ctx)
    playlist = res.playlist
    items = []
    fit = display.fit_override if display.fit_override in FIT_MODES else None
    group = display.group
    synced = bool(group is not None and group.sync_playback)
    if playlist is not None:
        for item in sorted(playlist.items, key=lambda i: (i.position, i.id)):
            entry = _item_entry(item, fit, synced)
            if entry is not None:
                items.append(entry)

    body = {
        "v": MANIFEST_SCHEMA,
        "heartbeat_s": int(current_app.config["PLAYER_HEARTBEAT_SECONDS"]),
        "display": {"id": display.id, "name": display.name},
        "playlist": {"id": playlist.id, "name": playlist.name} if playlist is not None else None,
        "items": items,
        "source": dict(res.source),
        "emergency": _emergency_entry(res),
        "settings": settings_payload(display),
    }
    if synced and items:
        # Synchronized playback: every screen of the group works out the current slide from the
        # shared server clock and the item durations, so no chatter is needed (static/player/
        # sync.js). The anchor is the Unix epoch: the same for every screen and every restart,
        # with nothing to store. Player controls follow the display's own setting: a viewer who uses one leaves the
        # shared timeline, and the player rejoins it by itself after `settings.resume_after_s`.
        body["sync"] = {"group_id": group.id, "anchor_ms": 0,
                        "cycle_ms": sum(max(1000, int(e["duration_ms"])) for e in items)}
        # Next, previous, seek and pause move the timeline for the whole group (player/transport.py).
        from .transport import load as load_transport
        transport = load_transport(group)
        if transport is not None:
            body["sync"]["transport"] = transport
    body["version"] = manifest_version(body)
    body["server_time_ms"] = int(time.time() * 1000)
    return body
