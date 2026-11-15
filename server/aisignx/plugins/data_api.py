"""Plugin data for screens, on the player API (/api/player/v1; CSRF- and setup-exempt).

    GET /plugins/<media_id>/data               the cached data of a plugin item
    GET /plugins/<media_id>/assets/<asset_id>  one image of that data

Same trust as media_file: any paired display may read any plugin item's data. Private
config fields and secrets never appear here. The admin preview routes (routes.py)
answer with the same functions.

Data body: {"v":1, "fetched_at_ms", "stale", "error", "refresh_s", "data", "assets":
[{id, mime, size}]}. "error" is a code (timeout, http_503, ...), never upstream text.
An ETag (content hash) answers If-None-Match with 304. A cold miss answers
503 {"error":"warming"} (or "upstream_unavailable" while backing off) with Retry-After.
"""
import hashlib
import json
import re

from flask import Response, jsonify, request, send_file

from ..extensions import db
from ..models import Media
from ..player import api_bp
from ..player.api import api_error
from ..player.auth import display_required
from . import service
from .cache import ASSET_MIMES

ASSET_ID = re.compile(r"^[0-9a-f]{32}$")
DATA_VERSION = 1


def plugin_and_config(media: Media | None):
    """(plugin, config) of an enabled plugin item with data and a valid config, or None."""
    plugin = service.plugin_for(media)
    if plugin is None or plugin.data is None or not service.is_enabled(plugin):
        return None
    config = service.stored_config(media, plugin)
    return None if config is None else (plugin, config)


def data_response(plugin, config):
    """The data endpoint answer for one (plugin, config)."""
    served, refresh_s = service.serve_data(plugin, config)
    if served.status != "ok":
        message = ("The data is being fetched. Try again shortly." if served.status == "warming"
                   else "The data source is not answering. Try again later.")
        return api_error(503, served.status, message, retry_after=served.retry_after)
    entry = served.entry
    body = {
        "v": DATA_VERSION,
        "fetched_at_ms": int(float(entry.get("fetched_at") or 0) * 1000),
        "stale": bool(served.stale),
        "error": entry.get("last_error_code") or None,
        "refresh_s": refresh_s,
        "data": entry.get("data"),
        "assets": [{"id": a["id"], "mime": a["mime"], "size": a["size"]}
                   for a in entry.get("assets") or []],
    }
    raw = json.dumps(body, sort_keys=True, separators=(",", ":"))
    etag = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    if request.if_none_match.contains(etag):
        resp = Response(status=304)
    else:
        resp = Response(raw, mimetype="application/json")
    resp.set_etag(etag)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def asset_response(plugin, config, asset_id: str):
    """One asset listed in the current or previous good data of (plugin, config)."""
    if not ASSET_ID.match(asset_id or ""):
        return api_error(404, "not_found", "No such asset.")
    entry = service.cached_entry(plugin, config) or {}
    listed = [a for a in (entry.get("assets") or []) + (entry.get("prev_assets") or [])
              if isinstance(a, dict) and a.get("id") == asset_id]
    if not listed or listed[0].get("mime") not in ASSET_MIMES:
        return api_error(404, "not_found", "No such asset.")
    from .cache import get_cache

    path = get_cache().asset_path(asset_id)
    if not path.is_file():
        return api_error(404, "not_found", "No such asset.")
    resp = send_file(path, mimetype=listed[0]["mime"], conditional=True, etag=asset_id,
                     max_age=86400)
    resp.headers["Cache-Control"] = "private, max-age=86400, immutable"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


def _not_found():
    return api_error(404, "not_found", "No such plugin data.")


@api_bp.get("/plugins/<int:media_id>/data")
@display_required
def plugin_data(media_id: int):
    found = plugin_and_config(db.session.get(Media, media_id))
    return _not_found() if found is None else data_response(*found)


@api_bp.get("/plugins/<int:media_id>/assets/<asset_id>")
@display_required
def plugin_asset(media_id: int, asset_id: str):
    found = plugin_and_config(db.session.get(Media, media_id))
    return _not_found() if found is None else asset_response(*found, asset_id)
