"""RSS / Atom parsing for the news feed plugin: feed bytes in, plain-text items out.

Nothing here touches the network or the file system. feedparser gets a BytesIO, never the
raw bytes: a bytes or str argument may be taken as a URL or a file name. Entity
declarations in the prolog are removed before parsing (no XXE, no billion laughs, even
when feedparser falls back to its loose parser). All markup is reduced to plain text;
screens never receive HTML.

Images are decoded with Pillow from an allowlist of raster formats (no SVG), refused
above MAX_PIXELS before any pixel data is loaded, and re-encoded to WebP, which drops
metadata and anything a polyglot file carried.
"""
import calendar
import html
import io
import re
import unicodedata
import warnings
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

MAX_ITEMS = 30          # parsed per feed; each item config takes the first max_items
TITLE_MAX = 300
SUMMARY_MAX = 600
FEED_TITLE_MAX = 120
LINK_MAX = 1000         # longer links get no QR code
MAX_PIXELS = 40_000_000
IMAGE_MAX_WIDTH = 1280
IMAGE_MAX_HEIGHT = 1280
IMAGE_FORMATS = ("JPEG", "PNG", "WEBP", "GIF")
WEBP_QUALITY = 80

_PROLOG_END = re.compile(rb"<[A-Za-z_]")
_DOCTYPE = re.compile(rb"<!DOCTYPE(?:[^\[>]|\[.*?\])*>", re.S | re.I)
_ENTITY = re.compile(rb"<!ENTITY", re.I)
_SPACE = re.compile(r"\s+")
_IMG_SRC = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*["']([^"'>\s]+)["']""", re.I)
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "iframe", "object",
                        "svg", "math", "head", "title"})
_BLOCK_TAGS = frozenset({"p", "br", "div", "li", "ul", "ol", "tr", "h1", "h2", "h3", "h4",
                         "h5", "h6", "blockquote", "section", "article", "figure", "hr"})


class FeedError(ValueError):
    """The bytes are not a usable feed (code: parse_error)."""


# --- text -------------------------------------------------------------------------------

class _TextOnly(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self.skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_startendtag(self, tag, attrs):
        if tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self.skip = max(0, self.skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def plain_text(value, limit: int) -> str:
    """Markup-free, single-line text of at most `limit` characters (with an ellipsis)."""
    if not isinstance(value, str) or not value:
        return ""
    if "<" in value or "&" in value:
        parser = _TextOnly()
        try:
            parser.feed(value)
            parser.close()
            value = "".join(parser.parts)
        except Exception:  # malformed markup: fall back to removing anything tag-like
            value = html.unescape(re.sub(r"<[^>]*>", " ", value))
    value = "".join(" " if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp")
                    and c not in "‍" else c for c in value)
    value = _SPACE.sub(" ", value).strip()
    if len(value) > limit:
        cut = value[:limit - 1]
        space = cut.rfind(" ")
        if space > limit * 0.6:
            cut = cut[:space]
        value = cut.rstrip(" ,.;:-") + "…"
    return value


def web_url(value, base: str = "") -> str | None:
    """An absolute http(s) URL without user info, or None."""
    if not isinstance(value, str) or not value.strip() or len(value) > 2048:
        return None
    try:
        url = urljoin(base, html.unescape(value.strip())) if base else value.strip()
        parts = urlsplit(url)
        parts.port  # noqa: B018 (raises ValueError for a bad port)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    if parts.username is not None or parts.password is not None:
        return None
    if any(c.isspace() or ord(c) < 32 for c in url):
        return None
    return url


def host_of(url) -> str:
    try:
        host = (urlsplit(url).hostname or "") if isinstance(url, str) else ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


# --- feeds ------------------------------------------------------------------------------

def defuse(body: bytes) -> bytes:
    """Remove the DOCTYPE (with its internal subset) from the prolog, so no entity is
    ever declared. Refuses a prolog that still declares one (a malformed DOCTYPE), and
    any UTF-16/32 document that declares one anywhere."""
    if b"\x00" in body[:64]:  # UTF-16/32 (rare): no surgery, just refuse declarations
        if _ENTITY.search(body.replace(b"\x00", b"")):
            raise FeedError("entity declarations are not allowed")
        return body
    start = body[:65536].upper().find(b"<!DOCTYPE")
    if start != -1 and not _PROLOG_END.search(body, 0, start):
        match = _DOCTYPE.match(body, start)
        if match:
            body = body[:start] + body[match.end():]
    root = _PROLOG_END.search(body)
    if _ENTITY.search(body, 0, root.start() if root else len(body)):
        raise FeedError("entity declarations are not allowed")
    return body


def _published_ms(entry) -> int | None:
    for name in ("published_parsed", "updated_parsed", "created_parsed"):
        value = entry.get(name)
        if value:
            try:
                return int(calendar.timegm(value)) * 1000
            except (TypeError, ValueError, OverflowError):
                continue
    return None


def _summary(entry) -> str:
    for value in (entry.get("summary"),
                  *(c.get("value") for c in entry.get("content") or [] if isinstance(c, dict))):
        text = plain_text(value, SUMMARY_MAX)
        if text:
            return text
    return ""


def _image_url(entry, base: str) -> str | None:
    """The entry's picture: media:content, media:thumbnail, an image enclosure, or the
    first <img> in its HTML."""
    candidates = []
    for media in entry.get("media_content") or []:
        kind = str(media.get("medium") or media.get("type") or "")
        if not kind or kind == "image" or kind.startswith("image/"):
            candidates.append(media.get("url"))
    candidates += [t.get("url") for t in entry.get("media_thumbnail") or []]
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and str(link.get("type") or "").startswith("image/"):
            candidates.append(link.get("href"))
    for enclosure in entry.get("enclosures") or []:
        if str(enclosure.get("type") or "").startswith("image/"):
            candidates.append(enclosure.get("href") or enclosure.get("url"))
    for value in (entry.get("summary"),
                  *(c.get("value") for c in entry.get("content") or [] if isinstance(c, dict))):
        found = _IMG_SRC.search(value) if isinstance(value, str) else None
        if found:
            candidates.append(found.group(1))
    for candidate in candidates:
        url = web_url(candidate, base)
        if url:
            return url
    return None


def parse_feed(body: bytes, base_url: str, content_type: str = "") -> dict:
    """{feed: {title, link_host}, items: [{title, summary, published_ms, link, image_url,
    source}]}, at most MAX_ITEMS items, in feed order. Raises FeedError."""
    import feedparser

    headers = {"content-location": base_url}
    if content_type:
        headers["content-type"] = content_type
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        parsed = feedparser.parse(io.BytesIO(defuse(body)), response_headers=headers,
                                  resolve_relative_uris=True, sanitize_html=True)
    feed = parsed.get("feed") or {}
    entries = parsed.get("entries") or []
    if not entries and not feed.get("title"):
        raise FeedError("not a feed")
    items = []
    for entry in entries:
        title = plain_text(entry.get("title"), TITLE_MAX)
        summary = _summary(entry)
        if not title and not summary:
            continue
        link = web_url(entry.get("link"), base_url)
        source = entry.get("source") or {}
        items.append({
            "title": title or summary[:TITLE_MAX],
            "summary": summary if title else "",
            "published_ms": _published_ms(entry),
            "link": link if link and len(link) <= LINK_MAX else None,
            "image_url": _image_url(entry, link or base_url),
            "source": plain_text(source.get("title") if isinstance(source, dict) else "",
                                 FEED_TITLE_MAX),
        })
        if len(items) >= MAX_ITEMS:
            break
    feed_link = web_url(feed.get("link"), base_url) or base_url
    return {"feed": {"title": plain_text(feed.get("title"), FEED_TITLE_MAX),
                     "link_host": host_of(feed_link)},
            "items": items}


# --- images and QR codes ----------------------------------------------------------------

def to_webp(raw: bytes) -> bytes:
    """Re-encode a JPEG, PNG, WebP or GIF picture as WebP, at most 1280 px wide and high.
    Raises ValueError for anything else, and before decoding when the declared size is
    above MAX_PIXELS."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            img = Image.open(io.BytesIO(raw), formats=IMAGE_FORMATS)
            width, height = img.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise ValueError("image too large")
            img.draft("RGB", (IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT))  # JPEG: decode smaller
            img.load()
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P", "PA") else "RGB")
            img.thumbnail((IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT), Image.LANCZOS)
            out = io.BytesIO()
            img.save(out, "WEBP", quality=WEBP_QUALITY, method=4)
    except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError,
            Image.DecompressionBombWarning, EOFError) as exc:
        raise ValueError(f"not a usable image ({type(exc).__name__})") from None
    return out.getvalue()


def qr_png(link: str) -> bytes | None:
    """A PNG QR code of an http(s) link (dark on white, quiet zone included), or None."""
    url = web_url(link)
    if not url or len(url) > LINK_MAX:
        return None
    import segno

    try:
        code = segno.make(url, error="m", micro=False)
    except (ValueError, segno.DataOverflowError):
        return None
    out = io.BytesIO()
    code.save(out, kind="png", scale=8, border=2, dark="#000000", light="#ffffff")
    return out.getvalue()
