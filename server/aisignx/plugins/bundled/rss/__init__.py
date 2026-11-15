"""News feed plugin (RSS / Atom): server hooks.

fetch() runs on the plugin worker only. The feed URL is a user URL (any public host,
http or https, ports 80/443/8080/8443) fetched through the framework's guarded client
with a conditional GET. Hosts named in the admin setting allow_private_hosts may be on a
private network. Parsing, text cleaning and image handling live in parse.py.

Shared state in the plugin cache (sub-entries, never sent to screens):
    feed state per feed URL   {etag, last_modified, parsed}  for If-None-Match / 304
    image state per image URL {id} or {failed: true}         so a picture is fetched,
                                                             checked and re-encoded once
Data sent to screens:
    {feed: {title, link_host}, items: [{title, summary, published_ms, source, image?, qr?}]}
image and qr are asset ids (WebP and PNG). The feed URL and item links never leave the
server (a link only as a QR code picture).
"""
import hashlib
import logging
import time
from urllib.parse import urlsplit

from aisignx.plugins.fetch import USER_URL_PORTS, FetchError, RateLimited
from aisignx.plugins.schema import SchemaError
from aisignx.plugins.service import FetchResult

from . import parse

log = logging.getLogger(__name__)

FEED_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MAX_BYTES = 2 * 1024 * 1024
MAX_IMAGES = 10                 # per feed
IMAGE_OK_TTL = 7 * 86400
IMAGE_FAILED_TTL = 3600         # a broken picture is tried again after an hour
FEED_STATE_TTL = 7 * 86400
IMAGE_RESERVE_S = 3.0           # stop fetching pictures this close to the deadline
_ACCEPT_FEED = ("application/rss+xml, application/atom+xml, application/feed+xml, "
                "application/xml;q=0.9, text/xml;q=0.9, */*;q=0.5")
_ACCEPT_IMAGE = "image/webp, image/jpeg, image/png, image/gif;q=0.8"


# --- hooks ------------------------------------------------------------------------------

def normalize(config, ctx):
    """Check what the schema cannot: the port, and the fragment (dropped)."""
    url = config.get("url") or ""
    try:
        parts = urlsplit(url)
        port = parts.port or (443 if parts.scheme.lower() == "https" else 80)
    except ValueError:
        raise SchemaError("Enter an http or https address.", "url") from None
    if port not in USER_URL_PORTS:
        raise SchemaError("Feeds must use port 80, 443, 8080 or 8443.", "url")
    if parts.fragment:
        config["url"] = url.split("#", 1)[0]
    return config


def playable(media, config, ctx):
    return bool(config.get("url"))


def fetch(config, ctx):
    feed = _feed(config["url"], ctx)
    wanted = feed["items"][:int(config.get("max_items") or 8)]
    assets, items, images = {}, [], 0
    for entry in wanted:
        item = {"title": entry["title"], "summary": entry["summary"],
                "published_ms": entry["published_ms"], "source": entry.get("source") or ""}
        if config.get("show_images") and entry.get("image_url") and images < MAX_IMAGES:
            images += 1
            asset = _image(entry["image_url"], ctx)
            if asset is not None:
                assets[asset.id] = asset
                item["image"] = asset.id
        if config.get("show_qr") and entry.get("link"):
            png = parse.qr_png(entry["link"])
            if png:
                asset = ctx.asset(png, "image/png")
                assets[asset.id] = asset
                item["qr"] = asset.id
        items.append(item)
    data = {"feed": dict(feed["feed"]), "items": items}
    return FetchResult(data=data, assets=list(assets.values()))


# --- the feed ---------------------------------------------------------------------------

def _store():
    from aisignx.plugins.cache import get_cache

    return get_cache()


def _sub_key(kind: str, url: str) -> str:
    return hashlib.sha256(f"rss:{kind}:{url}".encode("utf-8")).hexdigest()


def _feed(url: str, ctx) -> dict:
    """The parsed feed: re-used on 304 Not Modified, parsed afresh otherwise."""
    store = _store()
    key = _sub_key("feed", url)
    state, _fresh = store.sub_get(key)
    state = state if isinstance(state, dict) and isinstance(state.get("parsed"), dict) else None
    headers = {"Accept": _ACCEPT_FEED}
    if state and state.get("etag"):
        headers["If-None-Match"] = state["etag"]
    if state and state.get("last_modified"):
        headers["If-Modified-Since"] = state["last_modified"]
    resp = ctx.http.get(url, headers=headers, user_url=True, max_bytes=FEED_MAX_BYTES)
    if resp.status == 304 and state:
        store.sub_put(key, state, FEED_STATE_TTL)
        return state["parsed"]
    resp.raise_for_status()
    if resp.status != 200:
        raise FetchError(f"http_{resp.status}")
    try:
        parsed = parse.parse_feed(resp.body, resp.url or url,
                                  str(resp.headers.get("content-type") or "")[:200])
    except parse.FeedError:
        raise FetchError("parse_error") from None
    state = {"etag": _header(resp, "etag"), "last_modified": _header(resp, "last-modified"),
             "parsed": parsed}
    store.sub_put(key, state, FEED_STATE_TTL)
    return parsed


def _header(resp, name: str) -> str | None:
    value = str(resp.headers.get(name) or "").strip()
    return value if value and len(value) <= 200 and value.isprintable() else None


# --- pictures ---------------------------------------------------------------------------

def _image(url: str, ctx):
    """The picture as a WebP Asset, or None (failed, refused, or no time left)."""
    store = _store()
    key = _sub_key("image", url)
    state, fresh = store.sub_get(key)
    if fresh and isinstance(state, dict):
        if state.get("failed"):
            return None
        asset = _stored_asset(store, state.get("id"))
        if asset is not None:
            return asset
    if ctx.deadline - time.monotonic() < IMAGE_RESERVE_S:
        return None
    try:
        resp = ctx.http.get(url, headers={"Accept": _ACCEPT_IMAGE}, user_url=True,
                            max_bytes=IMAGE_MAX_BYTES)
        resp.raise_for_status()
        asset = ctx.asset(parse.to_webp(resp.body), "image/webp")
    except RateLimited:
        return None  # not a property of the picture: try again on the next refresh
    except (FetchError, ValueError) as exc:
        log.info("rss: picture skipped (%s)", getattr(exc, "code", "bad_image"))
        store.sub_put(key, {"failed": True}, IMAGE_FAILED_TTL)
        return None
    store.write_asset(asset.id, asset.data)
    store.sub_put(key, {"id": asset.id}, IMAGE_OK_TTL)
    return asset


def _stored_asset(store, asset_id):
    from aisignx.plugins.service import Asset

    if not isinstance(asset_id, str) or len(asset_id) != 32:
        return None
    try:
        raw = store.asset_path(asset_id).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest()[:32] != asset_id:
        return None
    return Asset(asset_id, "image/webp", raw)
