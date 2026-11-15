"""Weather plugin (P2): plugin.json, config, both providers against recorded fixtures, the
fetch hook through the framework cache, and the stale path. Never touches the network:
the guarded client's DNS and request steps are replaced, so host allowlists, rate limits
and secret redaction still run for real."""
import json
import shutil
import subprocess
import time
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from aisignx.extensions import db
from aisignx.models import Setting
from aisignx.plugins import fetch as fetch_mod
from aisignx.plugins.bundled.weather import providers, wmo

from .conftest import bearer, make_user
from .plugin_fixtures import add_item, paired_with, papp, pclient  # noqa: F401

API = "/api/player/v1"
FIXTURES = Path(__file__).parent / "fixtures" / "weather"
JS = Path(__file__).parent / "js"
STATIC = Path(providers.__file__).parents[3] / "static" / "player" / "plugins" / "weather"
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc).timestamp()
OSLO = ZoneInfo("Europe/Oslo")
CONFIG = {"location": "59.9139,10.7522", "units": "metric", "layout": "full", "days": 5,
          "show_hourly": True, "theme": "auto", "refresh": 600}
KEY = "fixture-openmeteo-key-123"


def _fixture(name):
    return (FIXTURES / name).read_bytes()


def _set(app, key, value):
    with app.app_context():
        Setting.set(key, value)
        db.session.commit()


class Upstream:
    """Stands in for DNS and the HTTP exchange of fetch.Client."""

    def __init__(self, monkeypatch):
        self.calls = []
        self.fail = None
        self.routes = {}
        upstream = self

        def addresses(client, host, port):
            return ["93.184.216.34"]

        def request(client, scheme, host, port, parts, addrs, headers, cap, shown_url):
            upstream.calls.append({"host": host, "path": parts.path, "query": parts.query,
                                   "headers": dict(headers), "shown": shown_url,
                                   "ua": client.user_agent})
            if upstream.fail:
                return fetch_mod.Response(upstream.fail, {}, b"internal.host said no")
            status, hdrs, body = upstream.routes[parts.path]
            return fetch_mod.Response(status, dict(hdrs), body)

        monkeypatch.setattr(fetch_mod.Client, "_addresses", addresses)
        monkeypatch.setattr(fetch_mod.Client, "_request", request)
        self.routes = {
            "/v1/forecast": (200, {}, _fixture("open_meteo_forecast.json")),
            "/weatherapi/locationforecast/2.0/complete": (200, {}, _fixture("met_locationforecast.json")),
            "/weatherapi/sunrise/3.0/sun": (200, {}, _fixture("met_sunrise.json")),
        }


@pytest.fixture
def upstream(monkeypatch):
    providers.clear_memo()
    yield Upstream(monkeypatch)
    providers.clear_memo()


def _plugin(app):
    return app.extensions["aisignx_plugins"].get("weather")


def _data(client, token, media_id):
    resp = client.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    return resp.status_code, resp.get_json(), resp.get_data(as_text=True)


# --- plugin.json and config ---------------------------------------------------------------

def test_plugin_json_loads(papp):
    plugin = _plugin(papp)
    assert plugin is not None and plugin.render == "frame" and plugin.version == "3.0.0"
    assert list(plugin.files) == ["index.html", "main.js", "icons.js", "style.css"]
    assert set(plugin.data.hosts) == {"api.open-meteo.com", "customer-api.open-meteo.com",
                                      "api.met.no"}
    assert plugin.data.ttl == 600 and plugin.data.min_ttl == 300
    assert list(plugin.data.keys) == ["location.lat", "location.lon", "units"]
    provider = plugin.setting_field("provider")
    met = [o for o in provider["options"] if o["value"] == "met-norway"]
    assert met and met[0]["requires_contact"] is True and provider["default"] == "open-meteo"
    assert [s["env"] for s in plugin.secrets] == ["AISIGNX_OPENMETEO_KEY"]
    raw = (Path(providers.__file__).parent / "plugin.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")


def test_defaults_and_validation(papp):
    from aisignx.plugins import schema, service

    plugin = _plugin(papp)
    assert schema.defaults(plugin.schema) == {
        "location": None, "units": "metric", "layout": "full", "days": 5, "show_hourly": True,
        "theme": "auto", "refresh": 600}
    with papp.test_request_context():
        cfg, errors = service.validate_config(plugin, {"location": "59.91389,10.75221"})
        assert not errors
        assert cfg["location"]["lat"] == 59.9139 and cfg["location"]["lon"] == 10.7522
        assert {k: cfg[k] for k in ("units", "layout", "days", "theme", "refresh")} \
            == {"units": "metric", "layout": "full", "days": 5, "theme": "auto", "refresh": 600}
        _cfg, errors = service.validate_config(plugin, {"location": "Oslo",
                                                        "days": 9, "refresh": 60})
        assert {"location", "days", "refresh"} <= set(errors)
        _cfg, errors = service.validate_config(plugin, dict(CONFIG, units="kelvin"))
        assert "units" in errors


# --- providers ------------------------------------------------------------------------------

def test_wmo_mapping_from_met_symbols():
    assert wmo.from_met("clearsky_day") == (0, True)
    assert wmo.from_met("partlycloudy_polartwilight") == (2, False)
    assert wmo.from_met("lightrainshowersandthunder_night") == (95, False)
    assert wmo.from_met("lightssleetshowersandthunder_day") == (95, True)  # MET's own typo
    assert wmo.from_met("heavysnow") == (75, None)
    assert wmo.from_met("bogus_day") == (None, True)
    assert wmo.from_met(None) == (None, None)
    assert all(code in wmo.CODES for code in wmo.MET_SYMBOLS.values())
    assert wmo.clean(3) == 3 and wmo.clean(4) is None and wmo.clean(True) is None


def test_open_meteo_url_and_key():
    url = providers.open_meteo_url(59.913912345, 10.7522, "imperial", None)
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")
    assert "latitude=59.9139&" in url and "longitude=10.7522&" in url and "apikey" not in url
    assert "temperature_unit=fahrenheit" in url and "wind_speed_unit=mph" in url
    keyed = providers.open_meteo_url(1.5, 2, "metric", KEY)
    assert keyed.startswith("https://customer-api.open-meteo.com/") and f"apikey={KEY}" in keyed
    assert "latitude=1.5&" in keyed and "longitude=2&" in keyed


def test_parse_open_meteo_fixture():
    raw = json.loads(_fixture("open_meteo_forecast.json"))
    data = providers.parse_open_meteo(raw, "metric")
    assert set(data) == {"location", "units", "current", "hourly", "daily", "provider",
                         "attribution"}
    assert data["provider"] == "open-meteo" and "Open-Meteo" in data["attribution"]
    assert data["location"]["tz"] == "Europe/Oslo"
    assert data["units"] == {"temp": "°C", "wind": "km/h", "precip": "mm"}
    cur = data["current"]
    assert set(cur) == {"temp", "feels", "code", "is_day", "wind", "wind_dir", "humidity",
                        "precip"}
    assert cur["temp"] == 4.2 and cur["feels"] == 1.1 and cur["code"] == 3
    assert cur["is_day"] is True and cur["humidity"] == 81 and cur["wind_dir"] == 204
    assert len(data["hourly"]) == 12
    assert all(h["t"] > NOW * 1000 for h in data["hourly"])
    assert data["hourly"][0]["t"] == (NOW + 3600) * 1000
    assert set(data["hourly"][0]) == {"t", "temp", "code", "pop", "is_day"}
    days = data["daily"]
    assert [d["date"] for d in days] == [f"2026-11-{n}" for n in range(15, 22)]
    assert days[0]["code"] == 3 and days[1]["code"] == 61 and days[0]["tmax"] >= days[0]["tmin"]
    sunrise = datetime.fromtimestamp(days[0]["sunrise"] / 1000, OSLO)
    assert (sunrise.hour, sunrise.minute) == (8, 21)
    json.dumps(data)


def test_parse_open_meteo_tolerates_gaps():
    raw = json.loads(_fixture("open_meteo_forecast.json"))
    raw["current"]["temperature_2m"] = None
    raw["current"]["weather_code"] = 1234
    raw["hourly"]["temperature_2m"] = raw["hourly"]["temperature_2m"][:5]
    del raw["daily"]["sunrise"]
    data = providers.parse_open_meteo(raw, "metric")
    assert data["current"]["temp"] is None and data["current"]["code"] is None
    assert all(h["temp"] is None for h in data["hourly"])
    assert data["daily"][0]["sunrise"] is None


def test_parse_met_fixture_imperial():
    raw = json.loads(_fixture("met_locationforecast.json"))
    data = providers.parse_met(raw, "imperial", OSLO, NOW + 1)
    assert data["provider"] == "met-norway" and "MET Norway" in data["attribution"]
    assert data["units"] == {"temp": "°F", "wind": "mph", "precip": "in"}
    assert data["location"]["tz"] == "Europe/Oslo"
    first = raw["properties"]["timeseries"][1]["data"]["instant"]["details"]  # 12:00Z
    cur = data["current"]
    assert cur["temp"] == round(first["air_temperature"] * 9 / 5 + 32, 1)
    assert cur["wind"] == round(first["wind_speed"] * 2.23694, 1)
    assert cur["code"] == 3 and cur["is_day"] is True and cur["humidity"] == 80
    assert cur["feels"] < cur["temp"]  # wind chill
    hourly = data["hourly"]
    assert len(hourly) == 12 and hourly[0]["t"] == (NOW + 3600) * 1000
    thunder = [h for h in hourly if h["code"] == 95]  # 17:00 Oslo time
    assert thunder and datetime.fromtimestamp(thunder[0]["t"] / 1000, OSLO).hour == 17
    assert thunder[0]["is_day"] is False
    days = data["daily"]
    assert len(days) == 7 and days[0]["date"] == "2026-11-15"
    assert [d["code"] for d in days[1:4]] == [61, 81, 2]
    assert all(d["tmin"] <= d["tmax"] for d in days)
    assert days[0]["sunrise"] is None  # filled by met_sun


def test_met_sun_url_and_parse(upstream):
    url = providers.met_sun_url(59.9139, 10.7522, date(2026, 11, 15), OSLO)
    assert url == ("https://api.met.no/weatherapi/sunrise/3.0/sun?lat=59.9139&lon=10.7522"
                   "&date=2026-11-15&offset=%2B01%3A00")
    client = fetch_mod.Client(hosts=["api.met.no"])
    rise, sets = providers.met_sun(client, 59.9139, 10.7522, date(2026, 11, 15), OSLO)
    assert datetime.fromtimestamp(rise / 1000, OSLO).strftime("%H:%M") == "08:21"
    assert datetime.fromtimestamp(sets / 1000, OSLO).strftime("%H:%M") == "15:39"
    upstream.routes["/weatherapi/sunrise/3.0/sun"] = (200, {}, _fixture("met_sunrise_polar.json"))
    assert providers.met_sun(client, 69.6, 18.9, date(2026, 12, 21), OSLO) == [None, None]


def test_met_conditional_requests_and_expires(upstream):
    client = fetch_mod.Client(hosts=["api.met.no"])
    expires = "Sun, 15 Nov 2026 12:30:00 GMT"
    upstream.routes["/weatherapi/locationforecast/2.0/complete"] = (
        200, {"last-modified": "Sun, 15 Nov 2026 11:31:12 GMT", "expires": expires},
        _fixture("met_locationforecast.json"))
    data, ttl = providers.met_norway(client, 59.913912, 10.75226, "metric", OSLO, now=NOW)
    assert ttl == 1800 and data["current"]["code"] == 3
    call = upstream.calls[-1]
    assert call["query"] == "lat=59.9139&lon=10.7523" and "If-Modified-Since" not in call["headers"]
    upstream.routes["/weatherapi/locationforecast/2.0/complete"] = (
        304, {"expires": "Sun, 15 Nov 2026 14:00:00 GMT"}, b"")
    again, ttl = providers.met_norway(client, 59.913912, 10.75226, "metric", OSLO, now=NOW)
    assert upstream.calls[-1]["headers"]["If-Modified-Since"] == "Sun, 15 Nov 2026 11:31:12 GMT"
    assert again == data and ttl == providers.MET_MAX_TTL
    upstream.routes["/weatherapi/locationforecast/2.0/complete"] = (200, {}, b"{not json")
    providers.clear_memo()
    with pytest.raises(fetch_mod.FetchError) as err:
        providers.met_norway(client, 1, 2, "metric", OSLO, now=NOW)
    assert err.value.code == "parse_error"


# --- the hook through the framework ----------------------------------------------------------

def test_open_meteo_item_end_to_end(papp, pclient, upstream):
    make_user(papp, "admin", "admin")
    media_id = add_item(papp, "weather", dict(CONFIG, location={"q": "Oslo", "lat": 59.9139,
                                                                "lon": 10.7522,
                                                                "label": "Head office"}))
    token = paired_with(papp, media_id)
    item, = pclient.get(f"{API}/manifest", headers=bearer(token)).get_json()["items"]
    assert item["url"].startswith("/static/player/plugins/weather/index.html#c=")
    assert item["plugin"]["data"] == f"{API}/plugins/{media_id}/data"
    assert item["plugin"]["config"]["location"]["label"] == "Head office"
    status, body, _text = _data(pclient, token, media_id)
    assert status == 200 and body["stale"] is False and body["refresh_s"] == 600
    data = body["data"]
    assert data["location"] == {"label": "Head office", "lat": 59.9139, "lon": 10.7522,
                                "tz": "Europe/Oslo"}
    assert data["current"]["temp"] == 4.2 and len(data["daily"]) == 7
    call, = upstream.calls
    assert call["host"] == "api.open-meteo.com" and "apikey" not in call["query"]
    assert call["ua"].startswith("AiSignX/")


def test_open_meteo_key_is_used_and_never_leaks(tmp_path, monkeypatch, upstream):
    from .plugin_fixtures import make_plugin_app

    app = make_plugin_app(tmp_path, monkeypatch, AISIGNX_OPENMETEO_KEY=KEY)
    client = app.test_client()
    make_user(app, "admin", "admin")
    media_id = add_item(app, "weather", CONFIG)
    token = paired_with(app, media_id)
    manifest = client.get(f"{API}/manifest", headers=bearer(token)).get_data(as_text=True)
    status, _body, text = _data(client, token, media_id)
    assert status == 200
    call, = upstream.calls
    assert call["host"] == "customer-api.open-meteo.com" and f"apikey={KEY}" in call["query"]
    assert KEY not in call["shown"] and "apikey=REDACTED" in call["shown"]
    assert KEY not in manifest and KEY not in text
    with app.app_context():
        db.engine.dispose()


def test_met_norway_needs_contact(papp, pclient, upstream):
    make_user(papp, "admin", "admin")
    _set(papp, "plugins.weather.provider", "met-norway")
    media_id = add_item(papp, "weather", CONFIG)
    token = paired_with(papp, media_id)
    assert pclient.get(f"{API}/manifest", headers=bearer(token)).get_json()["items"] == []
    _set(papp, "plugins.ua_contact", "it@example.com")
    items = pclient.get(f"{API}/manifest", headers=bearer(token)).get_json()["items"]
    assert [i["media_id"] for i in items] == [media_id]
    status, body, _text = _data(pclient, token, media_id)
    assert status == 200 and body["data"]["provider"] == "met-norway"
    hosts = {c["host"] for c in upstream.calls}
    assert hosts == {"api.met.no"}
    assert all(c["ua"].endswith("; it@example.com)") for c in upstream.calls)
    suns = [c for c in upstream.calls if c["path"].endswith("/sun")]
    assert len(suns) == len(body["data"]["daily"])
    assert body["data"]["daily"][0]["sunrise"] is not None


def test_met_sunrise_is_shared_and_optional(papp, pclient, upstream):
    make_user(papp, "admin", "admin")
    _set(papp, "plugins.weather.provider", "met-norway")
    _set(papp, "plugins.ua_contact", "it@example.com")
    upstream.routes["/weatherapi/sunrise/3.0/sun"] = (500, {}, b"")
    first = add_item(papp, "weather", CONFIG)
    token = paired_with(papp, first)
    status, body, _text = _data(pclient, token, first)
    assert status == 200 and body["data"]["daily"][0]["sunrise"] is None


def test_failure_serves_the_last_good_copy(papp, pclient, upstream):
    make_user(papp, "admin", "admin")
    media_id = add_item(papp, "weather", CONFIG)
    token = paired_with(papp, media_id)
    assert _data(pclient, token, media_id)[0] == 200
    store = papp.extensions["aisignx_plugin_cache"]
    for path in store.root.glob("*.json"):
        entry = json.loads(path.read_text())
        entry["expires_at"] = time.time() - 1200
        path.write_text(json.dumps(entry))
    upstream.fail = 503
    status, body, text = _data(pclient, token, media_id)
    assert status == 200 and body["stale"] is True and body["error"] == "http_503"
    assert body["data"]["current"]["temp"] == 4.2 and "internal.host" not in text


def test_cold_failure_is_unavailable(papp, pclient, upstream):
    make_user(papp, "admin", "admin")
    media_id = add_item(papp, "weather", CONFIG)
    token = paired_with(papp, media_id)
    upstream.fail = 500
    resp = pclient.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    assert resp.status_code == 503 and resp.headers.get("Retry-After")


def test_nearby_items_share_one_fetch(papp, pclient, upstream):
    make_user(papp, "admin", "admin")
    a = add_item(papp, "weather", dict(CONFIG, location="59.9139,10.7522"))
    b = add_item(papp, "weather", dict(CONFIG, location="59.9101,10.7498", layout="compact"))
    token = paired_with(papp, a, b)
    assert _data(pclient, token, a)[0] == 200 and _data(pclient, token, b)[0] == 200
    assert len(upstream.calls) == 1


# --- front end (Node, fake DOM) --------------------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_front_end_renders_every_layout(tmp_path):
    om = providers.parse_open_meteo(json.loads(_fixture("open_meteo_forecast.json")), "metric")
    met = providers.parse_met(json.loads(_fixture("met_locationforecast.json")), "imperial",
                              OSLO, NOW + 1)
    for data in (om, met):
        data["location"].update(label="Data label", lat=59.9139, lon=10.7522)
    source = tmp_path / "data.json"
    source.write_text(json.dumps({"om": om, "met": met}), encoding="utf-8")
    proc = subprocess.run(["node", str(JS / "plugin_weather.js"), str(STATIC), str(source)],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)

    h = out["helpers"]
    assert h["temp"] == ["4°", "0°", "—", "-3°"]
    assert h["compass"] == ["N", "SSW", "N", "W", ""]
    assert h["localDate"] == ["2026-11-16", "2026-11-15"]
    assert h["upcoming"] == [(NOW + 3600) * 1000, (NOW + 7200) * 1000, (NOW + 10800) * 1000]
    assert h["futureDays"] == ["2026-11-17", "2026-11-18", "2026-11-19"]
    assert h["bounds"] == {"lo": -2, "hi": 10} and h["flat"] == {"lo": 3, "hi": 4}
    assert h["none"] is None and h["span"] == [20, 50]
    assert h["theme"] == ["theme-dark", "theme-auto mood-clear-night", "theme-auto mood-storm"]
    assert h["weekday"] == "Mon"

    full = out["full"]
    assert "ready" in full["calls"] and ["fallback", None, None] in full["calls"]
    assert ["attribution", om["attribution"]] in full["calls"] and ["stale", 0] in full["calls"]
    assert full["body"] == "wx-body theme-auto mood-cloud-day"
    assert full["wrap"] == "wx layout-full has-hours"
    assert full["place"] == ["Head office"]  # the item's own label, not the shared data's
    assert full["temp"] == ["4°"] and full["now_text"] == ["Overcast"]
    assert full["labels"][:3] == ["Feels like", "WindSSW", "Humidity"]
    assert full["tiles"][:4] == ["1°", "13 km/h", "81%", "84%"]
    assert len(full["hours"]) == 8 and full["days"][:2] == ["Today", "Mon"]
    assert len(full["days"]) == 5 and len(full["bars"]) == 5 and full["dots"] == 1
    assert full["timers"] == [15000]

    assert out["compact"]["wrap"] == "wx layout-compact no-hours"
    assert len(out["compact"]["days"]) == 7 and out["compact"]["tiles"] == []
    current = out["current"]
    assert current["wrap"] == "wx layout-current no-hours" and current["days"] == []
    assert current["body"] == "wx-body theme-light" and current["temp"] == ["43°"]
    assert ["stale", 1794740400000] in current["calls"]
    assert "Sunrise" in current["labels"] and "—" in current["tiles"]  # MET without sun data
    assert out["nohours"]["wrap"] == "wx layout-full no-hours" and out["nohours"]["hours"] == []

    later = out["later"]  # two days after the fetch: past hours and days are not shown
    assert later["days"][0] == "Today" and len(later["days"]) == 5
    assert later["hours"] == [] and later["wrap"] == "wx layout-full no-hours"

    empty = out["empty"]
    assert empty["calls"][0] == ["fallback", "Weather", "This screen is offline"]
    assert "ready" in empty["calls"] and empty["icons"] == 0
