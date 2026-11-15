"""Web Mercator maths and the drawing of radar slides with Pillow.

A View is the part of the world one slide shows: centred on a point, at a zoom level, in
one of three shapes. Its map is MAP_SIZES[aspect] Web Mercator pixels at that zoom, and
it is drawn SCALE times larger (OUT_SIZES), so base-map labels stay readable on a screen
across the room. Radar tiles may come from a lower zoom (RainViewer stops at 7) and are
scaled up to the same picture, so every frame lines up with the base map.

Everything here is pure: tiles come in as decoded images, pictures go out as WebP bytes.
"""
import io
import math
from dataclasses import dataclass

from PIL import Image, ImageOps

from ...fetch import FetchError

TILE = 256
MAX_LAT = 85.05112878
SCALE = 1.5
MAP_SIZES = {"landscape": (1280, 720), "portrait": (720, 1280), "square": (960, 960)}
OUT_SIZES = {k: (round(w * SCALE), round(h * SCALE)) for k, (w, h) in MAP_SIZES.items()}
MAX_TILE_SIDE = 1024           # larger "tiles" are refused before decoding
WEBP_QUALITY = 80
BACKGROUND = (12, 17, 26)      # where the base map has no tile (or none was drawn)
DARK_COLORS = {"black": (6, 9, 15), "mid": (28, 35, 47), "white": (72, 84, 101)}


def clamp_lat(lat: float) -> float:
    return max(-MAX_LAT, min(MAX_LAT, float(lat)))


def world_size(z: int) -> int:
    return TILE * (2 ** z)


def project(lat: float, lon: float, z: int) -> tuple:
    """Global pixel (x, y) of a point at zoom z (Web Mercator, 256-pixel tiles)."""
    size = world_size(z)
    lat = math.radians(clamp_lat(lat))
    x = (float(lon) + 180.0) / 360.0 * size
    y = (1.0 - math.log(math.tan(lat) + 1.0 / math.cos(lat)) / math.pi) / 2.0 * size
    return x, y


def unproject(x: float, y: float, z: int) -> tuple:
    """(lat, lon) of a global pixel at zoom z."""
    size = world_size(z)
    lon = x / size * 360.0 - 180.0
    lat = math.degrees(math.atan(math.sinh(math.pi * (1.0 - 2.0 * y / size))))
    return lat, lon


@dataclass(frozen=True)
class View:
    lat: float
    lon: float
    zoom: int
    aspect: str = "landscape"

    @property
    def map_size(self) -> tuple:
        return MAP_SIZES.get(self.aspect, MAP_SIZES["landscape"])

    @property
    def out_size(self) -> tuple:
        return OUT_SIZES.get(self.aspect, OUT_SIZES["landscape"])

    def box(self, z: int) -> tuple:
        """(x0, y0, x1, y1) of the view in global pixels at zoom z (z <= zoom)."""
        f = 2 ** (self.zoom - z)
        cx, cy = project(self.lat, self.lon, self.zoom)
        w, h = self.map_size
        return ((cx - w / 2) / f, (cy - h / 2) / f, (cx + w / 2) / f, (cy + h / 2) / f)

    def tile_span(self, z: int) -> tuple:
        """(tx0, ty0, tx1, ty1): the tile columns and rows the view touches at zoom z.
        Columns may fall outside 0..2^z-1 (the map wraps); rows outside are blank."""
        x0, y0, x1, y1 = self.box(z)
        return (math.floor(x0 / TILE), math.floor(y0 / TILE),
                math.floor((x1 - 1e-6) / TILE), math.floor((y1 - 1e-6) / TILE))

    def tiles(self, z: int) -> list:
        """The (z, x, y) tiles to fetch, x wrapped into range, rows off the map left out."""
        tx0, ty0, tx1, ty1 = self.tile_span(z)
        n = 2 ** z
        out = []
        for ty in range(ty0, ty1 + 1):
            if not 0 <= ty < n:
                continue
            for tx in range(tx0, tx1 + 1):
                key = (z, tx % n, ty)
                if key not in out:
                    out.append(key)
        return out


def decode_tile(raw: bytes) -> Image.Image | None:
    """An RGBA tile image, or None for an empty answer. Refuses huge or broken images."""
    if not raw:
        return None
    try:
        img = Image.open(io.BytesIO(raw))
        if img.format not in ("PNG", "JPEG", "WEBP"):
            raise FetchError("parse_error")
        if max(img.size) > MAX_TILE_SIDE:
            raise FetchError("too_large")
        img.load()
    except (OSError, ValueError, Image.DecompressionBombError):
        raise FetchError("parse_error") from None
    img = img.convert("RGBA")
    if img.size != (TILE, TILE):
        img = img.resize((TILE, TILE), Image.Resampling.BILINEAR)
    return img


def compose(view: View, z: int, tiles: dict, *, opaque: bool) -> Image.Image:
    """The view drawn from tiles {(z, x, y): RGBA image or None} at zoom z, scaled to
    view.out_size. opaque: a base map (RGB on BACKGROUND); else a transparent overlay."""
    tx0, ty0, tx1, ty1 = view.tile_span(z)
    n = 2 ** z
    size = ((tx1 - tx0 + 1) * TILE, (ty1 - ty0 + 1) * TILE)
    canvas = Image.new("RGBA", size, BACKGROUND + (255,) if opaque else (0, 0, 0, 0))
    for ty in range(max(ty0, 0), min(ty1, n - 1) + 1):
        for tx in range(tx0, tx1 + 1):
            tile = tiles.get((z, tx % n, ty))
            if tile is None:
                continue
            at = ((tx - tx0) * TILE, (ty - ty0) * TILE)
            if opaque:
                canvas.alpha_composite(tile, at)
            else:
                canvas.paste(tile, at)
    x0, y0, x1, y1 = view.box(z)
    box = (x0 - tx0 * TILE, y0 - ty0 * TILE, x1 - tx0 * TILE, y1 - ty0 * TILE)
    if opaque:
        return canvas.convert("RGB").resize(view.out_size, Image.Resampling.LANCZOS, box=box)
    # Resize with premultiplied alpha, so transparent pixels do not bleed dark fringes.
    return (canvas.convert("RGBa").resize(view.out_size, Image.Resampling.BILINEAR, box=box)
            .convert("RGBA"))


def darken(img: Image.Image) -> Image.Image:
    """A dim blue-grey version of a base map, so radar colours stand out."""
    gray = ImageOps.grayscale(img.convert("RGB"))
    return ImageOps.colorize(gray, **DARK_COLORS)


def webp(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=WEBP_QUALITY, method=4)
    return buf.getvalue()


def has_echo(img: Image.Image) -> bool:
    """True when a radar overlay has any visible pixel (used by tests and the summary)."""
    alpha = img.getchannel("A") if img.mode == "RGBA" else None
    return bool(alpha and alpha.getbbox())
