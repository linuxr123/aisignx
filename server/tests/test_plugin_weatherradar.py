"""Weather radar plugin: plugin.json, tile maths, drawing, the sources against recorded
answers, picture reuse, the data endpoint and failures. No test touches the network:
ctx.http is replaced by a fake that serves fixtures and tiles drawn here."""
import io
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from PIL import Image

from aisignx.extensions import db
from aisignx.models import Media, Setting
from aisignx.plugins import service
from aisignx.plugins.bundled.weatherradar import sources, tiles
from aisignx.plugins.fetch import FetchError, RateLimited, Response

from .conftest import bearer, login, make_user
from .plugin_fixtures import add_item, make_plugin_app, paired_with

HERE = Path(__file__).resolve().parent
FIX = HERE / "fixtures" / "weatherradar"
STATIC = HERE.parent / "aisignx" / "static" / "player" / "plugins" / "weatherradar"
BUNDLED = HERE.parent / "aisignx" / "plugins" / "bundled" / "weatherradar"
API = "/api/player/v1"
PLACE = {"q": "", "lat": "41.59", "lon": "-93.62", "label": "Des Moines"}
CONFIG = {"location": PLACE, "zoom": 7, "frames": 4, "frame_ms": 600, "aspect": "landscape",
          "darken_basemap": True, "alerts": False, "refresh": 300}
RADAR_RGBA = (40, 140, 255, 210)
IEM_LATEST = int(datetime(2025, 11, 15, 12, 35, tzinfo=timezone.utc).timestamp())


def png(color, size=(256, 256), fmt="PNG") -> bytes:
    buf = io.BytesIO()
    Image.new("RGBA" if fmt == "PNG" else "RGB", size, color).save(buf, fmt)
    return buf.getvalue()


class FakeHTTP:
    """Stands in for ctx.http. Radar tiles are coloured only where the place is."""

    def __init__(self, lat=41.59, lon=-93.62):
        self.lat, self.lon = lat, lon
        self.calls = []          # (url, allow_hosts, headers)
        self.maps = json.loads((FIX / "rainviewer_maps.json").read_text(encoding="utf-8"))
        self.fail = {}           # host -> status code or FetchError code
        self.fail_paths = set()  # url substrings answered with 500
        self.rate_limited = 0    # raise RateLimited this many times first

    def _radar_tile(self, z, x, y):
        cx, cy = tiles.project(self.lat, self.lon, z)
        hit = (int(cx // 256), int(cy // 256)) == (x, y)
        return png(RADAR_RGBA if hit else (0, 0, 0, 0))

    def get(self, url, *, headers=None, allow_hosts=(), **_kw):
        if self.rate_limited:
            self.rate_limited -= 1
            raise RateLimited()
        parts = urlsplit(url)
        host = parts.hostname
        self.calls.append((url, tuple(allow_hosts), dict(headers or {})))
        assert parts.scheme == "https"
        failure = self.fail.get(host)
        if isinstance(failure, str):
            raise FetchError(failure)
        if failure or any(s in url for s in self.fail_paths):
            return Response(failure or 500, {}, b"", url)
        if host == "api.rainviewer.com":
            return Response(200, {}, json.dumps(self.maps).encode(), url)
        if host == "tilecache.rainviewer.com":
            z, x, y = (int(v) for v in parts.path.split("/")[5:8])
            return Response(200, {}, self._radar_tile(z, x, y), url)
        if host == "mesonet.agron.iastate.edu":
            if parts.path.endswith(".json"):
                return Response(200, {}, (FIX / "iem_meta.json").read_bytes(), url)
            z, x, y = (int(v) for v in parts.path.rsplit(".", 1)[0].split("/")[-3:])
            return Response(200, {}, self._radar_tile(z, x, y), url)
        if host == "tile.openstreetmap.org":
            assert allow_hosts == ("tile.openstreetmap.org",)
            z, x, y = (int(v) for v in parts.path.rsplit(".", 1)[0].split("/")[-3:])
            return Response(200, {}, png((240, 236, 228, 255) if (x + y) % 2 else
                                         (170, 211, 223, 255)), url)
        if host == "api.weather.gov":
            return Response(200, {}, (FIX / "nws_alerts.json").read_bytes(), url)
        raise FetchError("blocked_host")

    def urls(self, host):
        return [c[0] for c in self.calls if urlsplit(c[0]).hostname == host]


@pytest.fixture
def rapp(tmp_path, monkeypatch):
    app = make_plugin_app(tmp_path, monkeypatch, PLUGIN_DIRS=[])
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def fake(monkeypatch):
    http = FakeHTTP()
    monkeypatch.setattr(service.Context, "http", property(lambda self: http))
    return http


def _plugin(app):
    return app.extensions["aisignx_plugins"].get("weatherradar")


def _item(app, **config):
    return add_item(app, "weatherradar", dict(CONFIG, **config), name="Radar")


def _data(app, client, media_id, token=None):
    token = token or paired_with(app, media_id)
    return client.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))


def _run(app, **config):
    """fetch() for a config, straight through the hook (no data endpoint)."""
    plugin = _plugin(app)
    with app.test_request_context():
        cfg, errors = service.validate_config(plugin, dict(CONFIG, **config))
        assert not errors, errors
        return plugin.hook("fetch")(cfg, service.Context(plugin))


# --- plugin.json and files -------------------------------------------------------------

def test_plugin_loads_with_defaults(rapp):
    plugin = _plugin(rapp)
    assert plugin.render == "frame" and plugin.files == ("index.html", "main.js", "style.css")
    assert plugin.data.fetch_budget_s == 120 and plugin.data.min_ttl == 300
    assert set(plugin.data.hosts) >= {"api.rainviewer.com", "tilecache.rainviewer.com",
                                      "mesonet.agron.iastate.edu", "tile.openstreetmap.org",
                                      "api.weather.gov"}
    assert plugin.data.rate["tilecache.rainviewer.com"] == 120
    assert plugin.data.rate["tile.openstreetmap.org"] == 60
    assert plugin.data.rate["mesonet.agron.iastate.edu"] == 120
    with rapp.app_context():
        assert service.setting_values(plugin) == {
            "source": "rainviewer", "basemap_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            "basemap_attribution": "© OpenStreetMap contributors"}
        cfg, errors = service.validate_config(plugin, {"location": PLACE})
    assert not errors and cfg["darken_basemap"] is False  # an unticked box
    from aisignx.plugins import schema

    cfg = schema.defaults(plugin.schema)  # what the form starts with
    assert cfg["zoom"] == 7 and cfg["frames"] == 8 and cfg["frame_ms"] == 600
    assert cfg["aspect"] == "landscape" and cfg["darken_basemap"] is True
    assert cfg["alerts"] is False and cfg["refresh"] == 300


def test_config_bounds_and_location_required(rapp):
    plugin = _plugin(rapp)
    with rapp.test_request_context():
        _cfg, errors = service.validate_config(plugin, dict(CONFIG, zoom=11, frames=2,
                                                            frame_ms=100, refresh=60))
        assert {"zoom", "frames", "frame_ms", "refresh"} <= set(errors)
        _cfg, errors = service.validate_config(plugin, dict(CONFIG, location=None))
        assert "location" in errors


def test_basemap_url_setting_pattern(rapp):
    fld = _plugin(rapp).setting_field("basemap_url")
    from aisignx.plugins import schema

    ok = ["https://tiles.example.org/{z}/{x}/{y}.png", "https://a.example/osm/{z}/{x}/{y}@2x.png"]
    bad = ["http://tiles.example.org/{z}/{x}/{y}.png", "https://10.0.0.1:8080/{z}/{x}/{y}.png",
           "https://tiles.example.org/{z}/{x}/{y}.png?key=1", "https://user@x.org/{z}/{x}/{y}",
           "https://tiles.example.org/{x}/{y}.png"]
    for value in ok:
        assert schema.validate_value(fld, value, check_refs=False)[1] is None, value
    for value in bad:
        assert schema.validate_value(fld, value, check_refs=False)[1] is not None, value


def test_files_are_utf8_without_bom_and_small():
    for path in list(STATIC.iterdir()) + [p for p in BUNDLED.iterdir() if p.is_file()]:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        raw.decode("utf-8")
        assert raw.count(b"\n") < 800, path
    assert (STATIC / "main.js").read_bytes().count(b"\n") < 250
    text = (STATIC / "main.js").read_text(encoding="utf-8")
    assert "innerHTML" not in text and "eval(" not in text and "setAttribute(\"style\"" not in text
    assert "setTimeout" not in text and "setInterval" not in text  # ctx.every / ctx.later only


# --- tile maths and drawing --------------------------------------------------------------

def test_projection():
    assert tiles.project(0, 0, 0) == pytest.approx((128, 128))
    x, y = tiles.project(51.5074, -0.1278, 7)  # London: tile 63/42 at zoom 7
    assert (int(x // 256), int(y // 256)) == (63, 42)
    lat, lon = tiles.unproject(*tiles.project(-33.87, 151.21, 9), 9)
    assert (lat, lon) == pytest.approx((-33.87, 151.21), abs=1e-9)
    assert tiles.project(89.9, 0, 3)[1] == pytest.approx(0, abs=1e-6)  # clamped to the edge


def test_view_tiles():
    view = tiles.View(41.59, -93.62, 7, "landscape")
    x0, y0, x1, y1 = view.box(7)
    assert (x1 - x0, y1 - y0) == pytest.approx((1280, 720))
    assert view.out_size == (1920, 1080)
    assert 15 <= len(view.tiles(7)) <= 24
    assert len(view.tiles(5)) <= 4  # radar from a lower zoom needs far fewer tiles
    assert tiles.View(41.59, -93.62, 7, "portrait").out_size == (1080, 1920)
    assert tiles.View(41.59, -93.62, 7, "square").out_size == (1440, 1440)
    wrap = tiles.View(0, 179.9, 5, "landscape").tiles(5)
    assert all(0 <= x < 32 for _z, x, _y in wrap) and {0, 31} <= {x for _z, x, _y in wrap}
    polar = tiles.View(85, 0, 5, "portrait").tiles(5)
    assert all(0 <= y < 32 for _z, _x, y in polar)


def test_compose_aligns_radar_with_the_place():
    http = FakeHTTP()
    view = tiles.View(41.59, -93.62, 8, "landscape")
    got = {k: tiles.decode_tile(http._radar_tile(*k)) for k in view.tiles(7)}
    img = tiles.compose(view, 7, got, opaque=False)
    assert img.size == (1920, 1080) and img.mode == "RGBA"
    r, g, b, a = img.getpixel((960, 540))
    assert a > 150 and b > 200 and r < 80  # the place is under the radar echo
    assert img.getpixel((0, 0))[3] == 0     # far from it there is none
    assert tiles.has_echo(img)


def test_drawing_is_deterministic():
    http = FakeHTTP()
    view = tiles.View(41.59, -93.62, 7, "square")

    def base():
        got = {k: tiles.decode_tile(png((240, 236, 228, 255))) for k in view.tiles(7)}
        return tiles.webp(tiles.darken(tiles.compose(view, 7, got, opaque=True)))

    def radar():
        got = {k: tiles.decode_tile(http._radar_tile(*k)) for k in view.tiles(7)}
        return tiles.webp(tiles.compose(view, 7, got, opaque=False))

    assert base() == base() and radar() == radar()
    dark = Image.open(io.BytesIO(base())).convert("L")
    assert dark.getextrema()[1] < 110  # the dark map really is dark
    assert Image.open(io.BytesIO(base())).format == "WEBP"


def test_decode_tile_refuses_bad_images():
    assert tiles.decode_tile(b"") is None
    assert tiles.decode_tile(png((1, 2, 3, 4), (512, 512))).size == (256, 256)
    with pytest.raises(FetchError) as exc:
        tiles.decode_tile(png((1, 2, 3, 4), (2048, 2048)))
    assert exc.value.code == "too_large"
    for raw in (b"<svg xmlns='http://www.w3.org/2000/svg'/>", b"\x89PNG\r\n\x1a\nbroken",
                png((1, 2, 3), (8, 8), "GIF")):
        with pytest.raises(FetchError) as exc:
            tiles.decode_tile(raw)
        assert exc.value.code == "parse_error"


# --- sources -----------------------------------------------------------------------------

def test_rainviewer_frames_ignore_bad_paths_and_the_answer_host():
    http = FakeHTTP()
    got = sources.rainviewer_frames(http, 3)
    assert [f.t for f in got] == [1763207400, 1763208000, 1763208600]
    assert all(f.template.startswith("https://tilecache.rainviewer.com/v2/radar/") for f in got)
    assert got[-1].template.endswith("/256/{z}/{x}/{y}/2/1_1.png")
    assert len(sources.rainviewer_frames(http, 12)) == 6  # the bad path is dropped
    http.maps = {"radar": {"past": []}}
    with pytest.raises(FetchError):
        sources.rainviewer_frames(http, 3)


def test_iem_frames_use_the_latest_time():
    http = FakeHTTP()
    got = sources.iem_frames(http, 3)
    assert [f.t for f in got] == [IEM_LATEST - 600, IEM_LATEST - 300, IEM_LATEST]
    assert got[0].template.endswith("nexrad-n0q-900913-m10m/{z}/{x}/{y}.png")
    assert got[-1].template.endswith("nexrad-n0q-900913/{z}/{x}/{y}.png")
    assert all(f.reuse_id for f in got)
    assert len(sources.iem_frames(http, 12)) == 11
    http.fail["mesonet.agron.iastate.edu"] = 503
    guessed = sources.iem_frames(http, 2, now=1763209800)
    assert [f.t for f in guessed] == [1763209200, 1763209500]
    assert not any(f.reuse_id for f in guessed)  # a guessed time is never reused


def test_alerts_parsed_sorted_and_deduplicated():
    http = FakeHTTP()
    got = sources.alerts(http, 41.591234, -93.62)
    assert http.calls[-1][0].endswith("point=41.5912,-93.6200")
    assert http.calls[-1][2]["Accept"] == "application/geo+json"
    assert [a["event"] for a in got] == ["Severe Thunderstorm Warning", "Winter Weather Advisory"]
    assert got[0]["severity"] == "Severe" and "  " not in got[0]["headline"]
    assert got[1]["ends_ms"] == int(datetime(2025, 11, 16, tzinfo=timezone.utc).timestamp()) * 1000
    http.fail["api.weather.gov"] = 400  # outside the US
    assert sources.alerts(http, 51.5, 0.0) == []
    http.fail["api.weather.gov"] = "timeout"
    assert sources.alerts(http, 41.5, -93.6) == []


# --- fetch ---------------------------------------------------------------------------------

def test_fetch_data_shape_and_radar_zoom_cap(rapp, fake):
    result = _run(rapp, zoom=10)
    data = result.data
    assert data["source"] == "rainviewer" and data["zoom"] == 10 and data["radar_zoom"] == 7
    assert [f["t"] for f in data["frames"]] == [1763206800000, 1763207400000, 1763208000000,
                                                1763208600000]
    ids = {a.id for a in result.assets}
    assert data["basemap"] in ids and all(f["asset"] in ids for f in data["frames"])
    assert len(ids) == len(result.assets) and all(a.mime == "image/webp" for a in result.assets)
    assert data["attribution"] == "Radar: RainViewer · Map © OpenStreetMap contributors"
    assert data["alerts"] == [] and data["size"] == [1920, 1080] and data["aspect"] == "landscape"
    radar = fake.urls("tilecache.rainviewer.com")
    assert radar and all("/256/7/" in u for u in radar)  # RainViewer stops at zoom 7
    base = fake.urls("tile.openstreetmap.org")
    assert base and all(urlsplit(u).path.startswith("/10/") for u in base)
    assert not fake.urls("evil.example.com") and not fake.urls("api.weather.gov")


def test_pictures_are_reused_between_refreshes(rapp, fake):
    first = _run(rapp)
    base_calls, radar_calls = len(fake.urls("tile.openstreetmap.org")), len(
        fake.urls("tilecache.rainviewer.com"))
    per_frame = radar_calls // 4
    fake.maps["radar"]["past"].append({"time": 1763209200, "path": "/v2/radar/1763209200"})
    second = _run(rapp)
    assert len(fake.urls("tile.openstreetmap.org")) == base_calls     # map drawn once
    assert len(fake.urls("tilecache.rainviewer.com")) == radar_calls + per_frame  # one new frame
    assert second.data["basemap"] == first.data["basemap"]
    assert [f["asset"] for f in second.data["frames"][:3]] == [
        f["asset"] for f in first.data["frames"][1:]]
    # A pruned picture is drawn again, with the same bytes and so the same id.
    with rapp.app_context():
        from aisignx.plugins.cache import get_cache
        get_cache().asset_path(first.data["basemap"]).unlink()
    third = _run(rapp)
    assert third.data["basemap"] == first.data["basemap"]
    assert len(fake.urls("tile.openstreetmap.org")) == 2 * base_calls


def test_same_fixture_same_pictures_after_a_cache_clear(rapp, fake):
    first = _run(rapp)
    with rapp.app_context():
        from aisignx.plugins.cache import get_cache
        get_cache().clear()
    again = _run(rapp)
    assert again.data == first.data


def test_iem_source_alerts_and_custom_basemap(rapp, fake):
    with rapp.app_context():
        Setting.set("plugins.weatherradar.source", "iem-nexrad")
        Setting.set("plugins.weatherradar.basemap_url", "https://tiles.example.org/{z}/{x}/{y}.png")
        Setting.set("plugins.weatherradar.basemap_attribution", "© Example Maps")
        db.session.commit()
    seen = []
    real_get = fake.get

    def get(url, **kw):
        if urlsplit(url).hostname == "tiles.example.org":
            seen.append(kw.get("allow_hosts"))
            return Response(200, {}, png((90, 90, 90, 255)), url)
        return real_get(url, **kw)

    fake.get = get
    data = _run(rapp, zoom=10, frames=3, alerts=True).data
    assert data["source"] == "iem-nexrad" and data["radar_zoom"] == 9
    assert seen and all(h == ("tiles.example.org",) for h in seen)
    assert all("/9/" in u for u in fake.urls("mesonet.agron.iastate.edu") if u.endswith(".png"))
    assert [f["t"] for f in data["frames"]][-1] == IEM_LATEST * 1000
    assert [a["event"] for a in data["alerts"]][0] == "Severe Thunderstorm Warning"
    assert data["attribution"] == ("Radar: NOAA NEXRAD via Iowa Environmental Mesonet · "
                                   "Alerts: US National Weather Service · Map © Example Maps")


def test_a_failing_frame_is_skipped_but_a_failing_map_fails(rapp, fake):
    fake.fail_paths.add("/v2/radar/1763207400/")
    data = _run(rapp).data
    assert [f["t"] for f in data["frames"]] == [1763206800000, 1763208000000, 1763208600000]
    fake.fail_paths = {"/v2/radar/"}
    with pytest.raises(FetchError) as exc:
        _run(rapp, zoom=8)
    assert exc.value.code == "http_500"
    fake.fail_paths = {"tile.openstreetmap.org/9/"}
    with pytest.raises(FetchError):
        _run(rapp, zoom=9)


def test_rate_limit_waits_for_a_token(rapp, fake, monkeypatch):
    from aisignx.plugins.bundled import weatherradar

    monkeypatch.setattr(weatherradar, "RATE_WAIT_S", 0.01)
    fake.rate_limited = 3
    assert len(_run(rapp).data["frames"]) == 4


# --- the data endpoint ---------------------------------------------------------------------

def test_data_endpoint_and_assets(rapp, fake):
    make_user(rapp, "admin", "admin")
    media_id = _item(rapp)
    token = paired_with(rapp, media_id)
    resp = _data(rapp, rapp.test_client(), media_id, token)
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["refresh_s"] == 300 and body["error"] is None and body["stale"] is False
    listed = {a["id"]: a for a in body["assets"]}
    assert body["data"]["basemap"] in listed and len(body["data"]["frames"]) == 4
    asset = rapp.test_client().get(f"{API}/plugins/{media_id}/assets/{body['data']['basemap']}",
                                   headers=bearer(token))
    assert asset.status_code == 200 and asset.mimetype == "image/webp"
    assert Image.open(io.BytesIO(asset.data)).size == (1920, 1080)


def test_upstream_failure_serves_the_last_good_copy(rapp, fake):
    make_user(rapp, "admin", "admin")
    media_id = _item(rapp)
    client = rapp.test_client()
    token = paired_with(rapp, media_id)
    first = _data(rapp, client, media_id, token).get_json()
    with rapp.test_request_context():
        from aisignx.plugins.cache import get_cache
        media = db.session.get(Media, media_id)
        plugin = _plugin(rapp)
        key = service.cache_key(plugin, service.stored_config(media, plugin))
        store = get_cache()
        entry = store.read(key)
        entry["expires_at"] = time.time() - 4000
        store._write_json(store._path(key), entry)
    fake.fail["api.rainviewer.com"] = 503
    body = _data(rapp, client, media_id, token).get_json()
    assert body["stale"] is True and body["error"] == "http_503"
    assert body["data"] == first["data"]


def test_cold_failure_answers_upstream_unavailable(rapp, fake):
    make_user(rapp, "admin", "admin")
    media_id = _item(rapp)
    fake.fail["api.rainviewer.com"] = "timeout"
    resp = _data(rapp, rapp.test_client(), media_id)
    assert resp.status_code == 503 and resp.get_json()["error"] == "upstream_unavailable"
    assert int(resp.headers["Retry-After"]) >= 1


def test_manifest_entry_and_form(rapp, fake):
    make_user(rapp, "admin", "admin")  # past first-run setup
    make_user(rapp, "editor", "editor")
    client = rapp.test_client()
    login(client, "editor")
    resp = client.post("/plugins/new/weatherradar", data={
        "name": "Iowa radar", "duration": "20", "location.q": "41.59,-93.62",
        "location.lat": "", "location.lon": "", "location.label": "Des Moines",
        "zoom": "8", "frames": "6", "frame_ms": "500", "aspect": "portrait",
        "darken_basemap": "1", "refresh": "300"})
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:2000]
    with rapp.test_request_context():
        media = Media.query.filter_by(plugin_key="weatherradar").one()
        assert media.plugin_config["location"]["lat"] == 41.59
        assert media.plugin_config["aspect"] == "portrait" and media.default_duration == 20
        assert service.is_playable_plugin(media)
        fields = service.manifest_fields(media)
    assert fields["url"].startswith("/static/player/plugins/weatherradar/index.html#c=")
    assert fields["plugin"]["data"] == f"/api/player/v1/plugins/{media.id}/data"
    assert fields["plugin"]["refresh_s"] == 300 and fields["plugin"]["config"]["frame_ms"] == 500


# --- front end (Node) ------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_front_end_in_node():
    out = subprocess.run(["node", str(HERE / "js" / "plugin_weatherradar.js"), str(STATIC)],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["frameTimes"] == [500, 500, 1500]
    assert r["frameTimesClamped"] == [900, 6000]
    assert r["ago"] == ["Just now", "20 min ago", "1 h ago", "2 h 5 min ago"]
    assert r["liveAlerts"] == ["Tornado Warning"]
    assert r["severity"] == ["wr-alert wr-sev-severe", "wr-alert wr-sev-unknown"]
    run = r["run"]
    assert run["ready"] == 1 and run["fallbacks"] == [None] and run["frames"] == 3
    assert run["on"] == [0] and run["stepped"] == [1]  # oldest first, frame_ms each
    assert run["afterShow"] == [0]                      # shown: starts over
    assert run["newest"] == [2] and run["held"] == [2] and run["wrapped"] == [0]  # newest held 3x
    assert run["rel"] == "Latest · Just now" and run["clock"] == "12:10"
    assert run["paused"] == [0] and run["resumed"] == [1]
    assert run["place"] == "Des Moines" and run["stale"] == [0]
    assert run["attribution"] == "Radar: RainViewer · Map © OpenStreetMap contributors"
    assert run["alert"] == {"hidden": False, "event": "Tornado Warning"}  # the expired one is gone
    assert run["refresh"] == {"frames": 4, "assets": ["f" * 32], "ready": 2}
    assert run["badgeUnderTime"] is True
    assert r["warming"] == {"fallbacks": [["Weather radar", "Loading radar images…"]], "ready": 0}
    assert r["offline"] == {"fallbacks": [["Weather radar", "This screen is offline"]], "ready": 0}
