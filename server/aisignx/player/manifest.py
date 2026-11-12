"""What a screen should play: the manifest the player API, SSE hello and heartbeat share.

Fields may only ever be added; clients ignore keys they don't know. Phase 4 adds
`emergency` and `source`.

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
from ..timeutil import utcnow

MANIFEST_SCHEMA = 1
_FILE_TYPES = (MEDIA_IMAGE, MEDIA_VIDEO)


def resolve_playlist(display: Display, now: datetime) -> Playlist | None:
    """The playlist a display should show right now.

    Phase 3: the display's assigned playlist. Phase 4 replaces this with schedules,
    display groups and the default playlist.
    """
    return display.playlist


def _item_entry(item) -> dict | None:
    media = item.media
    if media is None:
        return None
    is_video = media.media_type == MEDIA_VIDEO
    if media.media_type in _FILE_TYPES:
        if not media.stored_name:
            return None
        entry = {
            "url": url_for("player_api.media_file", media_id=media.id, name=media.stored_name),
            "mime": media.mime_type,
            "sha256": media.checksum_sha256,
            "size": media.file_size,
            "width": media.width,
            "height": media.height,
        }
    elif media.media_type == MEDIA_WEB:
        url = (media.url or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            return None
        entry = {"url": url, "mime": None, "sha256": None, "size": None,
                 "width": None, "height": None}
    else:
        return None

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


def manifest_version(body: dict) -> str:
    """Content hash of a manifest body, ignoring version and server_time_ms."""
    stable = {k: v for k, v in body.items() if k not in ("version", "server_time_ms")}
    raw = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def build_manifest(display: Display, now: datetime | None = None) -> dict:
    """Build the manifest for one display. Needs a request context (for url_for)."""
    playlist = resolve_playlist(display, now or utcnow())
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
    }
    body["version"] = manifest_version(body)
    body["server_time_ms"] = int(time.time() * 1000)
    return body
