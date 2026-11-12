"""What a screen should play: the manifest the player API, SSE hello and heartbeat share.

Fields may only ever be added; clients ignore keys they don't know. Phase 4 added
`source` (why this playlist plays) and `emergency` (the overlay to show, or null).

`fit` keeps the domain vocabulary; the browser player maps it to CSS object-fit:
    fit -> contain, fill -> cover, stretch -> fill
"""
import hashlib
import json
import time
from datetime import datetime

from flask import current_app, url_for

from ..models import Display, Playlist
from ..models.media import MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_WEB

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
    if media is None:
        return False
    if media.media_type in _FILE_TYPES:
        return bool(media.stored_name)
    if media.media_type == MEDIA_WEB:
        return (media.url or "").strip().lower().startswith(("http://", "https://"))
    return False


def _item_entry(item) -> dict | None:
    if not is_playable(item):
        return None
    media = item.media
    is_video = media.media_type == MEDIA_VIDEO
    if media.media_type in _FILE_TYPES:
        entry = {
            "url": url_for("player_api.media_file", media_id=media.id, name=media.stored_name),
            "mime": media.mime_type,
            "sha256": media.checksum_sha256,
            "size": media.file_size,
            "width": media.width,
            "height": media.height,
        }
    else:  # web page
        entry = {"url": media.url.strip(), "mime": None, "sha256": None, "size": None,
                 "width": None, "height": None}

    media_ms = None
    if is_video and media.duration_seconds:
        media_ms = round(media.duration_seconds * 1000)
    return {
        "id": item.id,
        "media_id": media.id,
        "type": media.media_type,
        **entry,
        "duration_ms": item.effective_duration * 1000,
        "media_duration_ms": media_ms,
        "transition": item.effective_transition,  # may be "random"
        "fit": item.fit_mode,
        "muted": bool(item.mute) if is_video else True,
    }


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
    from ..schedules.resolve import resolve  # resolve imports is_playable from here

    res = resolve(display, now, ctx)
    playlist = res.playlist
    items = []
    if playlist is not None:
        for item in sorted(playlist.items, key=lambda i: (i.position, i.id)):
            entry = _item_entry(item)
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
    }
    body["version"] = manifest_version(body)
    body["server_time_ms"] = int(time.time() * 1000)
    return body
