"""Weather radar: animated past radar over a base map, drawn on the server.

fetch() runs on the plugin worker (budget 120 s). It draws one base map per place, zoom,
shape and tile server (kept 30 days, as the OpenStreetMap tile policy asks) and one
transparent WebP overlay per radar image (kept 3 hours), so a refresh only fetches the
radar images that are new since the last one. The screen just stacks the pictures.

Organization settings: source (rainviewer or iem-nexrad), basemap_url (an https XYZ tile
server; its host is allowed for these requests only) and basemap_attribution.

Data: {source, frames: [{t (epoch ms), asset}], basemap (asset id), attribution,
alerts: [{event, headline, severity, ends_ms}], zoom, radar_zoom, aspect, size: [w, h]}.
"""
import hashlib
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from ...cache import ASSET_MIMES, get_cache
from ...fetch import FetchError, RateLimited
from ...service import Asset, FetchResult
from . import sources, tiles

DEFAULT_BASEMAP = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
DEFAULT_BASEMAP_ATTRIBUTION = "© OpenStreetMap contributors"
BASEMAP_TTL = 30 * 86400
FRAME_TTL = 3 * 3600
DRAW_VERSION = 1          # bump when the drawing changes, so stored pictures are redrawn
BASEMAP_WORKERS = 2       # parallel base-map downloads (the OSM tile policy allows 2)
RADAR_WORKERS = 4         # parallel radar tile downloads
RATE_WAIT_S = 0.5         # wait for a token when a host's rate bucket is empty
MARGIN_S = 8.0            # stop starting new radar images this close to the deadline
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_EMPTY = (204, 404)       # no tile here: drawn as blank


def _wait_get(http, url: str, deadline: float, **kw):
    """http.get, waiting for the host's rate bucket (a cold start needs a burst)."""
    while True:
        try:
            return http.get(url, **kw)
        except RateLimited:
            if time.monotonic() + RATE_WAIT_S > deadline - MARGIN_S:
                raise
            time.sleep(RATE_WAIT_S)


def _tile_url(template: str, z: int, x: int, y: int) -> str:
    return template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))


def fetch_tiles(http, template: str, wanted: list, deadline: float, allow_hosts=(),
                workers: int = RADAR_WORKERS) -> dict:
    """{(z, x, y): RGBA image or None}. A missing tile (404) is blank; any other failure
    raises FetchError, so a half-drawn picture is never stored."""
    def one(key):
        resp = _wait_get(http, _tile_url(template, *key), deadline, allow_hosts=allow_hosts)
        if resp.status in _EMPTY:
            return key, None
        resp.raise_for_status()
        return key, tiles.decode_tile(resp.body)

    if len(wanted) <= 1:
        return dict(one(k) for k in wanted)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return dict(pool.map(one, wanted))


# --- stored pictures -------------------------------------------------------------------

def _stored(meta) -> Asset | None:
    """The stored picture a ctx.cache() value names, if its file is still intact."""
    if not isinstance(meta, dict) or not _ID_RE.match(str(meta.get("id") or "")):
        return None
    if meta.get("mime") not in ASSET_MIMES:
        return None
    try:
        raw = get_cache().asset_path(meta["id"]).read_bytes()
    except OSError:
        return None
    if hashlib.sha256(raw).hexdigest()[:32] != meta["id"]:
        return None
    return Asset(meta["id"], meta["mime"], raw)


def reuse(ctx, subkey: str, ttl: int, draw) -> Asset:
    """A picture drawn once and shared by every item and refresh: draw() -> bytes runs
    only when nothing is stored under subkey (or its file was pruned)."""
    made = {}

    def build():
        asset = ctx.asset(draw(), "image/webp")
        get_cache().write_asset(asset.id, asset.data)
        made["asset"] = asset
        return {"id": asset.id, "mime": asset.mime}

    meta = ctx.cache(subkey, ttl, build)
    if "asset" in made:
        return made["asset"]
    asset = _stored(meta)
    if asset is None:  # pruned meanwhile: draw it again (same bytes, so the same id)
        build()
        asset = made["asset"]
    return asset


def basemap(ctx, http, view: tiles.View, template: str, dark: bool) -> Asset:
    host = (urlsplit(template).hostname or "").lower()
    url_hash = hashlib.sha256(template.encode("utf-8")).hexdigest()[:16]
    subkey = (f"basemap:v{DRAW_VERSION}:{url_hash}:{int(dark)}:{view.lat:.2f}:{view.lon:.2f}:"
              f"{view.zoom}:{view.aspect}")

    def draw():
        got = fetch_tiles(http, template, view.tiles(view.zoom), ctx.deadline,
                          allow_hosts=(host,), workers=BASEMAP_WORKERS)
        img = tiles.compose(view, view.zoom, got, opaque=True)
        return tiles.webp(tiles.darken(img) if dark else img)

    return reuse(ctx, subkey, BASEMAP_TTL, draw)


def radar_frame(ctx, http, view: tiles.View, radar_zoom: int, frame) -> Asset:
    def draw():
        got = fetch_tiles(http, frame.template, view.tiles(radar_zoom), ctx.deadline)
        return tiles.webp(tiles.compose(view, radar_zoom, got, opaque=False))

    if not frame.reuse_id:
        asset = ctx.asset(draw(), "image/webp")
        return asset
    subkey = (f"frame:v{DRAW_VERSION}:{frame.reuse_id}:{view.lat:.2f}:{view.lon:.2f}:"
              f"{view.zoom}:{radar_zoom}:{view.aspect}")
    return reuse(ctx, subkey, FRAME_TTL, draw)


# --- hook --------------------------------------------------------------------------------

def view_for(config: dict) -> tiles.View:
    loc = config.get("location") or {}
    zoom = config.get("zoom") if isinstance(config.get("zoom"), int) else 7
    aspect = config.get("aspect") if config.get("aspect") in tiles.MAP_SIZES else "landscape"
    # Rounded like the cache key, so nearby screens share the same pictures.
    return tiles.View(round(tiles.clamp_lat(float(loc["lat"])), 2),
                      round(float(loc["lon"]), 2), max(1, min(int(zoom), 12)), aspect)


def _basemap_template(ctx) -> str:
    value = str(ctx.setting("basemap_url") or "").strip()
    return value or DEFAULT_BASEMAP


def attribution(source: str, basemap_text: str, with_alerts: bool) -> str:
    parts = [sources.ATTRIBUTION[sources.source_name(source)]]
    if with_alerts:
        parts.append("Alerts: US National Weather Service")
    parts.append(f"Map {basemap_text}" if basemap_text else "")
    return " · ".join(p for p in parts if p)


def fetch(config: dict, ctx) -> FetchResult:
    view = view_for(config)
    count = config.get("frames") if isinstance(config.get("frames"), int) else 8
    source = sources.source_name(ctx.setting("source"))
    radar_zoom = min(view.zoom, sources.max_zoom(source))
    http = ctx.http

    base = basemap(ctx, http, view, _basemap_template(ctx), bool(config.get("darken_basemap")))
    listed = sources.frames(http, source, count)
    drawn, failure = [], None
    for frame in reversed(listed):  # newest first: a cut-off loses the oldest images
        if drawn and time.monotonic() > ctx.deadline - MARGIN_S:
            break
        try:
            drawn.append((frame.t, radar_frame(ctx, http, view, radar_zoom, frame)))
        except FetchError as exc:
            failure = failure or exc
            if exc.code in ("timeout", "rate_limited"):
                break
    if not drawn:
        raise failure or FetchError("no_frames")
    drawn.sort(key=lambda item: item[0])

    alerts = sources.alerts(http, view.lat, view.lon) if config.get("alerts") else []
    basemap_text = str(ctx.setting("basemap_attribution") or "").strip()
    if _basemap_template(ctx) == DEFAULT_BASEMAP:
        basemap_text = basemap_text or DEFAULT_BASEMAP_ATTRIBUTION  # OSM's is required
    assets, seen = [base], {base.id}
    for _t, asset in drawn:
        if asset.id not in seen:
            seen.add(asset.id)
            assets.append(asset)
    data = {
        "source": source,
        "frames": [{"t": t * 1000, "asset": a.id} for t, a in drawn],
        "basemap": base.id,
        "attribution": attribution(source, basemap_text, bool(alerts)),
        "alerts": alerts,
        "zoom": view.zoom, "radar_zoom": radar_zoom, "aspect": view.aspect,
        "size": list(view.out_size),
    }
    return FetchResult(data, assets=assets)
