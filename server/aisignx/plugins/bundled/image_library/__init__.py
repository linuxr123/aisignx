"""Image library: a slideshow, Ken Burns show or collage of library images in one item.

A native plugin (static/player/plugins/image_library/native.js): the player draws the images
with <img> and the normal media URLs, so the display cookie, the HTTP cache and the kiosk
shells' media caches work as they do for image items. There is no external data.

The manifest block gets `media`: one entry per image to show, in order, each the manifest
file fields of an image item plus `media_id` (and `name` when captions are on). "newest" is
resolved when the manifest is built; an upload or a deletion is a Media change, so screens
get a new manifest. Deleted or non-image ids are skipped, and an item with no image left is
not playable.

In the admin preview (the plugins blueprint) the URLs point at the admin media route, since
the browser there has a login session and no display cookie.
"""
from flask import has_request_context, request, url_for

from aisignx.extensions import db
from aisignx.models import Media
from aisignx.models.media import MEDIA_IMAGE
from aisignx.plugins.schema import SchemaError

ADMIN_BLUEPRINT = "plugins"
GRID_TILES = {"2x2": 4, "3x3": 9}


def _images():
    """Image rows that have a file (uploads still being stored are left out)."""
    return Media.query.filter(Media.media_type == MEDIA_IMAGE, Media.stored_name.isnot(None))


def _connected(config: dict):
    """The images imported from the chosen image source, newest first or by name, at most source_count."""
    try:
        sid = int(config.get("image_source"))
    except (TypeError, ValueError):
        return _images().filter(Media.id < 0)
    q = _images().filter(Media.source_id == sid)
    if config.get("source_order") == "name":
        return q.order_by(db.func.lower(Media.name), Media.id)
    return q.order_by(Media.created_at.desc(), Media.id.desc())


def resolve(config: dict) -> list:
    """The Media rows to show, in order: the chosen ids that still exist, the newest, or those of a connected source."""
    if config.get("source") == "connected":
        return _connected(config).limit(int(config.get("source_count") or 50)).all()
    if config.get("source") == "newest":
        count = int(config.get("newest_count") or 1)
        return _images().order_by(Media.created_at.desc(), Media.id.desc()).limit(count).all()
    ids = [i for i in (config.get("media_ids") or []) if isinstance(i, int)]
    if not ids:
        return []
    found = {m.id: m for m in _images().filter(Media.id.in_(ids))}
    return [found[i] for i in ids if i in found]


def image_count(config: dict) -> int:
    """How many images the item shows (for "newest": the number asked for)."""
    if config.get("source") == "connected":
        return min(int(config.get("source_count") or 50), _connected(config).count())
    if config.get("source") == "newest":
        return int(config.get("newest_count") or 1)
    return len([i for i in (config.get("media_ids") or []) if isinstance(i, int)])


def normalize(config: dict, ctx) -> dict:
    if config.get("source") == "connected":
        if not config.get("image_source"):
            raise SchemaError("Choose an image source.", "image_source")
    elif config.get("source") != "newest" and not config.get("media_ids"):
        raise SchemaError("Choose at least one image.", "media_ids")
    return config


def playable(media, config: dict, ctx) -> bool:
    if config.get("source") == "connected":
        return _connected(config).with_entities(Media.id).first() is not None
    if config.get("source") == "newest":
        return _images().with_entities(Media.id).first() is not None
    ids = [i for i in (config.get("media_ids") or []) if isinstance(i, int)]
    return bool(ids) and _images().filter(Media.id.in_(ids)).with_entities(Media.id).first() is not None


def _admin_preview() -> bool:
    return has_request_context() and request.blueprint == ADMIN_BLUEPRINT


def _entry(media: Media, admin: bool, captions: bool) -> dict:
    if admin:
        entry = {"url": url_for("media.file", media_id=media.id), "mime": media.mime_type,
                 "sha256": media.checksum_sha256, "size": media.file_size,
                 "width": media.width, "height": media.height}
    else:
        from aisignx.player.manifest import file_entry

        entry = file_entry(media)
    entry["media_id"] = media.id
    if captions:
        entry["name"] = media.name
    return entry


def manifest_extra(media, config: dict, ctx) -> dict:
    admin = _admin_preview()
    captions = config.get("caption") == "name"
    return {"media": [_entry(m, admin, captions) for m in resolve(config)]}


def auto_duration(config: dict):
    """Seconds for the whole item when "auto" is on, else None (the form's duration)."""
    if not config.get("auto_duration"):
        return None
    seconds = int(config.get("seconds_per_image") or 8)
    count = max(1, image_count(config))
    if config.get("mode") == "collage":
        # Every image appears once, and every tile changes at least once.
        count = max(count, GRID_TILES.get(config.get("grid"), 4))
    return count * seconds
