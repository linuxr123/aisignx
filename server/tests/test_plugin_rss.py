"""News feed plugin (RSS / Atom): plugin.json, normalize, parsing, the fetch hook against
recorded fixtures (ctx.http replaced, never the network), hostile feeds and pictures,
conditional GET, the stale path and the manifest. JS helpers: tests/js/plugin_rss.js."""
import io
import json
import shutil
import socket
import struct
import subprocess
import time
import zlib
from pathlib import Path

import pytest
from PIL import Image

from aisignx.plugins import fetch as fetchmod
from aisignx.plugins import schema, service
from aisignx.plugins.bundled.rss import parse
from aisignx.plugins.fetch import FetchError, RateLimited, Response

from .conftest import bearer, make_user
from .plugin_fixtures import add_item, paired_with, papp, pclient  # noqa: F401

API = "/api/player/v1"
FIX = Path(__file__).parent / "fixtures" / "rss"
JS = Path(__file__).parent / "js"
STATIC = Path(__file__).resolve().parent.parent / "aisignx" / "static" / "player" / "plugins"
FEED_URL = "https://www.example-news.test/feed.xml"


def fixture(name: str) -> bytes:
    return (FIX / name).read_bytes()


def image_bytes(fmt="JPEG", size=(1600, 900), color=(200, 60, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, fmt)
    return buf.getvalue()


def pixel_bomb() -> bytes:
    """A tiny PNG whose header claims 9000 x 9000 pixels (81 MP)."""
    raw = bytearray(image_bytes("PNG", (8, 8)))
    ihdr = raw.index(b"IHDR")
    raw[ihdr + 4:ihdr + 12] = struct.pack(">II", 9000, 9000)
    crc = zlib.crc32(bytes(raw[ihdr:ihdr + 17])) & 0xFFFFFFFF
    raw[ihdr + 17:ihdr + 21] = struct.pack(">I", crc)
    return bytes(raw)


class FakeHTTP:
    """Stands in for ctx.http: routes[url] is a Response, bytes, an exception or a callable."""

    def __init__(self, routes=None):
        self.routes = dict(routes or {})
        self.calls = []

    def get(self, url, *, headers=None, redact=(), user_url=False, allow_hosts=(),
            max_bytes=None):
        self.calls.append({"url": url, "headers": dict(headers or {}), "user_url": user_url,
                           "max_bytes": max_bytes})
        route = self.routes.get(url)
        if callable(route) and not isinstance(route, Response):
            route = route(url, headers or {})
        if isinstance(route, Exception):
            raise route
        if route is None:
            return Response(404, {}, b"", url)
        if isinstance(route, bytes):
            return Response(200, {"content-type": "application/xml"}, route, url)
        return route

    def urls(self):
        return [c["url"] for c in self.calls]


@pytest.fixture
def http(monkeypatch):
    fake = FakeHTTP({
        FEED_URL: Response(200, {"content-type": "application/rss+xml", "etag": '"v1"',
                                 "last-modified": "Mon, 09 Nov 2026 09:00:00 GMT"},
                           fixture("rss2.xml"), FEED_URL),
        "https://img.example-news.test/ferry.jpg": image_bytes("JPEG"),
        "https://img.example-news.test/library.png": image_bytes("PNG", (300, 200)),
        "https://img.example-news.test/market.webp": image_bytes("WEBP", (640, 360)),
    })
    monkeypatch.setattr(service.Context, "http", property(lambda self: fake))
    return fake


def rss_config(app, **overrides):
    """A validated config as the form posts it (defaults, checkboxes included)."""
    with app.test_request_context():
        plugin = service.get_plugin("rss")
        raw = {**schema.defaults(plugin.schema), "url": FEED_URL, **overrides}
        cfg, errors = service.validate_config(plugin, raw)
    assert not errors, errors
    return cfg


def run_fetch(app, **overrides):
    """(data, {asset id: Asset}) from the plugin's fetch hook."""
    cfg = rss_config(app, **overrides)
    with app.test_request_context():
        plugin = service.get_plugin("rss")
        result = plugin.hook("fetch")(dict(cfg), service.Context(plugin))
    json.dumps(result.data)
    return result.data, {a.id: a for a in result.assets}


# --- plugin.json, defaults, normalize ---------------------------------------------------

def test_plugin_json_loads_with_defaults(papp):
    with papp.app_context():
        plugin = service.get_plugin("rss")
        assert plugin is not None and plugin.render == "frame" and not plugin.ends_itself
        assert plugin.files[0] == "index.html"
        for name in plugin.files:
            assert (STATIC / "rss" / name).is_file()
        assert plugin.data.min_ttl == 120 and plugin.data.max_bytes == 2 * 1024 * 1024
        assert "url" in plugin.data.keys
        fields = {f["name"]: f for f in plugin.schema}
        assert fields["url"]["private"] and fields["url"]["required"]
        assert fields["max_items"]["min"] == 1 and fields["max_items"]["max"] == 30
        assert fields["rotate_seconds"]["min"] == 5 and fields["rotate_seconds"]["max"] == 60
        assert fields["refresh"]["min"] == 120 and fields["refresh"]["default"] == 300
    cfg = rss_config(papp)
    assert cfg["mode"] == "list" and cfg["max_items"] == 8 and cfg["theme"] == "dark"
    assert cfg["show_images"] is True and cfg["show_qr"] is False
    with papp.app_context():
        public = service.public_config(service.get_plugin("rss"), cfg)
    assert "url" not in public and public["mode"] == "list"


@pytest.mark.parametrize("url,error", [
    ("https://news.example.test:8443/rss", None),
    ("http://news.example.test:8080/rss", None),
    ("https://news.example.test:9000/rss", "port"),
    ("ftp://news.example.test/rss", "http"),
    ("https://user:pw@news.example.test/rss", "url"),
])
def test_normalize_checks_ports_and_schemes(papp, url, error):
    with papp.test_request_context():
        cfg, errors = service.validate_config(service.get_plugin("rss"), {"url": url})
    if error is None:
        assert not errors and cfg["url"] == url
    else:
        assert "url" in errors


def test_normalize_drops_the_fragment(papp):
    assert rss_config(papp, url=FEED_URL + "#top")["url"] == FEED_URL


# --- parsing ----------------------------------------------------------------------------

def test_rss2_items_are_plain_text():
    feed = parse.parse_feed(fixture("rss2.xml"), FEED_URL, "application/rss+xml")
    assert feed["feed"] == {"title": "Harbour Town & Bay News", "link_host": "example-news.test"}
    titles = [i["title"] for i in feed["items"]]
    assert titles == ["Ferry timetable changes from Monday", "Library opens new reading room",
                      "Weekend market moves indoors",
                      "An item with only a summary becomes its own headline."]
    first, second, third = feed["items"][:3]
    assert first["summary"] == "The harbour ferry runs every 20 minutes. Evening sailings are unchanged."
    assert first["published_ms"] == 1794213000000
    assert first["image_url"] == "https://img.example-news.test/ferry.jpg"
    assert second["link"] == "https://www.example-news.test/articles/library"  # made absolute
    assert second["image_url"] == "https://img.example-news.test/library.png"
    assert second["published_ms"] == 1794153900000  # +0100 honoured
    assert third["link"] is None  # javascript: dropped
    assert third["image_url"] == "https://img.example-news.test/market.webp"
    text = json.dumps(feed)
    for bad in ("<", "script", "alert", "onerror", "javascript"):
        assert bad not in text


def test_atom_feed():
    feed = parse.parse_feed(fixture("atom.xml"), "https://council.example.test/feed")
    assert feed["feed"] == {"title": "City Council updates", "link_host": "council.example.test"}
    road, recycling = feed["items"]
    assert road["source"] == "Highways team"
    assert road["image_url"] == "https://council.example.test/img/road.jpg"
    assert road["published_ms"] == 1794302100000
    assert recycling["summary"] == "Collections move a day later during the holidays"
    assert recycling["published_ms"] == 1794207600000  # published wins over updated


@pytest.mark.parametrize("name", ["xxe.xml", "billion_laughs.xml"])
def test_entity_declarations_are_never_expanded(name):
    started = time.monotonic()
    try:
        feed = parse.parse_feed(fixture(name), FEED_URL)
    except parse.FeedError:
        return
    assert time.monotonic() - started < 5
    text = json.dumps(feed)
    assert "root:" not in text and "meta-data" not in text and "lollol" not in text
    assert len(text) < 2000


def test_malformed_or_utf16_entity_declarations_are_refused():
    with pytest.raises(parse.FeedError):
        parse.defuse(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b" <rss></rss>')
    utf16 = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE r [<!ENTITY a "b">]><rss/>'
    with pytest.raises(parse.FeedError):
        parse.defuse(utf16.encode("utf-16-le"))


def test_not_a_feed_and_garbage():
    for body in (fixture("not_a_feed.html"), b"", b"\x00\xff" * 100):
        with pytest.raises(parse.FeedError):
            parse.parse_feed(body, FEED_URL)


def test_huge_feed_is_capped_and_texts_are_cut():
    long_title = "Word " * 200
    long_summary = "Sentence " * 300
    entries = "".join(f"<item><title>{i} {long_title}</title><description>{long_summary}"
                      f"</description></item>" for i in range(500))
    body = f'<rss version="2.0"><channel><title>Big</title>{entries}</channel></rss>'.encode()
    feed = parse.parse_feed(body, FEED_URL)
    assert len(feed["items"]) == parse.MAX_ITEMS == 30
    item = feed["items"][0]
    assert len(item["title"]) <= parse.TITLE_MAX and item["title"].endswith("…")
    assert len(item["summary"]) <= parse.SUMMARY_MAX and item["summary"].endswith("…")


def test_plain_text_and_web_url_helpers():
    assert parse.plain_text("<p>A&nbsp;<b>bold</b></p><style>x{}</style>‮B\n\nC", 50) == "A bold B C"
    assert parse.plain_text(None, 10) == "" and parse.plain_text("x" * 20, 10) == "xxxxxxxxx…"
    assert parse.web_url("/a?b=1", "https://h.test/feed") == "https://h.test/a?b=1"
    for bad in ("javascript:alert(1)", "data:text/html,x", "https://u:p@h.test/", "file:///x",
                "https://h.test:99999/", "http://h .test/", ""):
        assert parse.web_url(bad) is None, bad
    assert parse.host_of("https://www.example.test/x") == "example.test"


# --- pictures and QR codes --------------------------------------------------------------

def test_pictures_are_reencoded_to_small_webp():
    out = parse.to_webp(image_bytes("JPEG", (3000, 1500)))
    img = Image.open(io.BytesIO(out))
    assert img.format == "WEBP" and img.size == (1280, 640)
    for fmt in ("PNG", "WEBP", "GIF"):
        assert Image.open(io.BytesIO(parse.to_webp(image_bytes(fmt, (40, 30))))).format == "WEBP"


def test_svg_and_non_images_are_refused():
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    for raw in (svg, b"<html></html>", b"", image_bytes("BMP", (10, 10)), image_bytes("TIFF", (10, 10))):
        with pytest.raises(ValueError):
            parse.to_webp(raw)


def test_pixel_bomb_is_refused_before_decoding(monkeypatch):
    from PIL import ImageFile

    def no_load(self):
        raise AssertionError("pixel data was decoded")

    monkeypatch.setattr(ImageFile.ImageFile, "load", no_load)
    with pytest.raises(ValueError, match="too large"):
        parse.to_webp(pixel_bomb())


def test_polyglot_payload_does_not_survive():
    raw = image_bytes("GIF", (20, 20)) + b"<script>alert(1)</script><?php echo 1; ?>"
    out = parse.to_webp(raw)
    assert out[:4] == b"RIFF" and out[8:12] == b"WEBP"
    assert b"script" not in out and b"php" not in out


def test_qr_codes_only_for_web_links():
    png = parse.qr_png("https://www.example-news.test/articles/ferry")
    assert Image.open(io.BytesIO(png)).format == "PNG"
    for bad in ("javascript:alert(1)", "mailto:a@b.test", "", "https://h.test/" + "x" * 1200):
        assert parse.qr_png(bad) is None


# --- the fetch hook ---------------------------------------------------------------------

def test_fetch_data_shape_and_assets(papp, http):
    data, assets = run_fetch(papp, show_qr=True)
    assert set(data) == {"feed", "items"}
    assert data["feed"] == {"title": "Harbour Town & Bay News", "link_host": "example-news.test"}
    assert len(data["items"]) == 4
    first = data["items"][0]
    assert set(first) == {"title", "summary", "published_ms", "source", "image", "qr"}
    assert assets[first["image"]].mime == "image/webp"
    assert assets[first["qr"]].mime == "image/png"
    assert "qr" not in data["items"][2]  # no usable link
    assert "image" not in data["items"][3]
    text = json.dumps(data)
    assert FEED_URL not in text and "articles/ferry" not in text  # links only inside QR codes
    feed_call = http.calls[0]
    assert feed_call["url"] == FEED_URL and feed_call["user_url"] is True
    assert feed_call["max_bytes"] == 2 * 1024 * 1024
    assert all(c["user_url"] and c["max_bytes"] == 2 * 1024 * 1024 for c in http.calls)


def test_fetch_respects_options(papp, http):
    data, assets = run_fetch(papp, max_items=2, show_images=False)
    assert len(data["items"]) == 2 and not assets
    assert http.urls() == [FEED_URL]


def test_at_most_ten_pictures_per_feed(papp, monkeypatch):
    items = "".join(f'<item><title>T{i}</title><enclosure url="https://img.test/{i}.png" '
                    f'type="image/png"/></item>' for i in range(14))
    routes = {FEED_URL: f'<rss version="2.0"><channel><title>F</title>{items}</channel></rss>'.encode()}
    for i in range(14):
        routes[f"https://img.test/{i}.png"] = image_bytes("PNG", (20 + i, 20))
    fake = FakeHTTP(routes)
    monkeypatch.setattr(service.Context, "http", property(lambda self: fake))
    data, assets = run_fetch(papp, max_items=14)
    assert sum("image" in i for i in data["items"]) == 10 and len(assets) == 10
    assert len(fake.calls) == 11


def test_conditional_get_reuses_the_parsed_feed(papp, http):
    first, _ = run_fetch(papp)

    def not_modified(url, headers):
        assert headers.get("If-None-Match") == '"v1"'
        assert headers.get("If-Modified-Since") == "Mon, 09 Nov 2026 09:00:00 GMT"
        return Response(304, {}, b"", url)

    http.routes[FEED_URL] = not_modified
    http.calls.clear()
    second, _ = run_fetch(papp)
    assert second == first
    assert http.urls() == [FEED_URL]  # pictures come from the cache


def test_broken_pictures_are_skipped_and_remembered(papp, http):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"/>'
    http.routes["https://img.example-news.test/ferry.jpg"] = svg
    http.routes["https://img.example-news.test/library.png"] = pixel_bomb()
    http.routes["https://img.example-news.test/market.webp"] = FetchError("too_large")
    data, assets = run_fetch(papp)
    assert not assets and not any("image" in i for i in data["items"])
    http.calls.clear()
    run_fetch(papp, max_items=7)  # another cache key, same pictures
    assert http.urls() == [FEED_URL]


def test_rate_limited_pictures_are_tried_again(papp, http):
    http.routes["https://img.example-news.test/ferry.jpg"] = RateLimited()
    data, _ = run_fetch(papp)
    assert "image" not in data["items"][0]
    http.routes["https://img.example-news.test/ferry.jpg"] = image_bytes("JPEG")
    data, _ = run_fetch(papp, max_items=7)
    assert "image" in data["items"][0]


def test_picture_on_a_private_address_is_blocked(papp, http, monkeypatch):
    """Pictures go through the real guarded client: a private address never connects."""
    real_getaddrinfo = socket.getaddrinfo

    def fake_dns(host, port, *args, **kwargs):
        if host == "img.example-news.test":
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", port))]
        return real_getaddrinfo(host, port, *args, **kwargs)

    def no_connect(*args, **kwargs):
        raise AssertionError("connected to a private address")

    monkeypatch.setattr(fetchmod.socket, "getaddrinfo", fake_dns)
    monkeypatch.setattr(fetchmod.Client, "_request", no_connect)
    guarded = fetchmod.Client(hosts=(), deadline=time.monotonic() + 10)
    feed_bytes = fixture("rss2.xml")

    def route(url, headers):
        if url == FEED_URL:
            return Response(200, {}, feed_bytes, url)
        return guarded.get(url, headers=headers, user_url=True)

    for url in list(http.routes):
        http.routes[url] = route
    data, assets = run_fetch(papp)
    assert not assets and len(data["items"]) == 4


@pytest.mark.parametrize("failure", [Response(500, {}, b"internal.host broke", FEED_URL),
                                     fixture("not_a_feed.html"), FetchError("timeout")])
def test_feed_failures_raise_codes(papp, http, failure):
    http.routes[FEED_URL] = failure
    with pytest.raises(FetchError) as info:
        run_fetch(papp)
    assert info.value.code in ("http_500", "parse_error", "timeout")
    assert "internal.host" not in str(info.value)


# --- through the framework: manifest, data endpoint, stale path --------------------------

def _rss_item(app, **config):
    make_user(app, "admin", "admin")
    media_id = add_item(app, "rss", rss_config(app, **config), name="Local news")
    return media_id, paired_with(app, media_id)


def test_manifest_entry_hides_the_feed_url(papp, pclient):
    media_id, token = _rss_item(papp)
    body = pclient.get(f"{API}/manifest", headers=bearer(token)).get_json()
    item, = body["items"]
    assert item["type"] == "plugin" and item["duration_ms"] == 30000
    assert item["url"].startswith("/static/player/plugins/rss/index.html#c=")
    assert item["plugin"]["data"] == f"{API}/plugins/{media_id}/data"
    assert item["plugin"]["refresh_s"] == 300
    assert "example-news" not in json.dumps(body)


def test_data_endpoint_then_stale_on_failure(papp, pclient, http):
    media_id, token = _rss_item(papp, show_qr=True)
    resp = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["stale"] is False and body["error"] is None and len(body["data"]["items"]) == 4
    assert FEED_URL not in resp.get_data(as_text=True)
    asset_id = body["data"]["items"][0]["image"]
    pic = pclient.get(f"{API}/plugins/{media_id}/assets/{asset_id}", headers=bearer(token))
    assert pic.status_code == 200 and pic.mimetype == "image/webp"

    http.routes[FEED_URL] = Response(503, {}, b"upstream secret text", FEED_URL)
    store = papp.extensions["aisignx_plugin_cache"]
    for path in store.root.glob("*.json"):
        entry = json.loads(path.read_text())
        if "data" in entry:
            entry["expires_at"] = time.time() - 1000
            path.write_text(json.dumps(entry))
    resp = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    body = resp.get_json()
    assert resp.status_code == 200 and body["stale"] is True and body["error"] == "http_503"
    assert len(body["data"]["items"]) == 4
    assert "secret" not in resp.get_data(as_text=True)


def test_cold_failure_is_503(papp, pclient, http):
    http.routes[FEED_URL] = FetchError("dns_error")
    media_id, token = _rss_item(papp)
    resp = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    assert resp.status_code == 503 and resp.headers.get("Retry-After")


# --- front end --------------------------------------------------------------------------

def test_runner_page_matches_the_template():
    html = (STATIC / "rss" / "index.html").read_text(encoding="utf-8")
    assert "connect-src 'none'" in html and "<script>" not in html and "style=" not in html
    js = (STATIC / "rss" / "main.js").read_text(encoding="utf-8")
    for banned in ("innerHTML", "eval(", "setAttribute(\"style\"", "setInterval(", "setTimeout("):
        assert banned not in js, banned


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_js_helpers():
    out = subprocess.run(["node", str(JS / "plugin_rss.js"), str(STATIC / "rss" / "main.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr + out.stdout
    assert out.stdout.strip().endswith("ok")
