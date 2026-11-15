"""Plugin admin page: toggles, settings, secrets, cache clear, geocoding."""
import json
import queue

import pytest

from aisignx.models import AuditLog, Setting
from aisignx.player.broker import get_broker
from aisignx.plugins import geo
from aisignx.plugins.fetch import FetchError, RateLimited, Response

from .conftest import login, make_user
from .plugin_fixtures import add_item, echo_hooks, padmin, papp, pclient  # noqa: F401


@pytest.fixture
def listener(papp):
    with papp.app_context():
        broker = get_broker()
        sub = broker.subscribe(999_999)
    yield sub
    broker.unsubscribe(sub)


@pytest.fixture(autouse=True)
def _fresh_geo():
    geo.clear_cache()
    from aisignx.plugins import routes

    routes._geo_calls.clear()
    yield


def _drain(sub):
    out = []
    while True:
        try:
            out.append(sub.queue.get_nowait())
        except queue.Empty:
            return out


def _audit(app, action):
    with app.app_context():
        return [json.loads(r.detail) if r.detail else None
                for r in AuditLog.query.filter_by(action=action).order_by(AuditLog.id)]


def test_list_page_for_admin_and_viewer(papp, pclient):
    make_user(papp, "admin", "admin")
    make_user(papp, "viewer", "viewer")
    login(pclient, "viewer")
    page = pclient.get("/plugins/")
    assert page.status_code == 200
    html = page.data.decode()
    assert "Echo" in html and "echo.example.com" in html and "Test data only." in html
    assert "/plugins/echo/settings" not in html and "Clear cache" not in html
    assert 'href="/plugins/"' in html and 'nav-label">Plugins</span>' in html  # nav link
    pclient.post("/logout")
    login(pclient, "admin")
    html = pclient.get("/plugins/").data.decode()
    assert "/plugins/echo/settings" in html and "Clear cache" in html and "User-Agent" in html


def test_toggle_publishes_manifest_and_is_audited(padmin, papp, listener):
    resp = padmin.post("/plugins/echo/settings", data={"provider": "basic"})
    assert resp.status_code == 303
    with papp.app_context():
        assert Setting.get("plugins.echo.enabled") == "0"
        assert Setting.get("plugins.echo.provider") == "basic"
    assert ("manifest", {}) in _drain(listener)
    detail, = _audit(papp, "plugin.settings")
    assert detail == {"plugin": "echo", "changed": ["plugins.echo.enabled",
                                                    "plugins.echo.provider"]}
    padmin.post("/plugins/echo/settings", data={"enabled": "1", "provider": "basic",
                                                "allow_private_hosts": ""})
    with papp.app_context():
        assert Setting.get("plugins.echo.enabled") == "1"
    assert _drain(listener) == [("manifest", {})]
    # Nothing changed: no audit row, no broadcast.
    padmin.post("/plugins/echo/settings", data={"enabled": "1", "provider": "basic",
                                                "allow_private_hosts": ""})
    assert _drain(listener) == [] and len(_audit(papp, "plugin.settings")) == 2


def test_settings_are_admin_only(papp, pclient):
    make_user(papp, "admin", "admin")
    make_user(papp, "ed", "editor")
    login(pclient, "ed")
    assert pclient.post("/plugins/echo/settings", data={}).status_code == 403
    assert pclient.post("/plugins/settings", data={}).status_code == 403
    assert pclient.post("/plugins/cache/clear").status_code == 403
    assert pclient.post("/plugins/nope/settings").status_code == 403
    with papp.app_context():
        assert Setting.get("plugins.echo.enabled") is None


def test_invalid_setting_values_are_refused(padmin, papp):
    padmin.post("/plugins/echo/settings", data={"enabled": "1", "provider": "bogus"})
    with papp.app_context():
        assert Setting.get("plugins.echo.provider") is None
    assert padmin.post("/plugins/nope/settings").status_code == 404


def test_contact_required_option(padmin, papp):
    resp = padmin.post("/plugins/echo/settings", data={"enabled": "1", "provider": "contact"},
                       follow_redirects=True)
    assert b"requires a contact" in resp.data
    with papp.app_context():
        assert Setting.get("plugins.echo.provider") is None
    padmin.post("/plugins/settings", data={"ua_contact": "it@example.com", "locale": "en-GB"})
    padmin.post("/plugins/echo/settings", data={"enabled": "1", "provider": "contact"})
    with papp.app_context():
        assert Setting.get("plugins.echo.provider") == "contact"
        assert Setting.get("plugins.locale") == "en-GB"
    # The contact cannot be removed while a provider needs it.
    resp = padmin.post("/plugins/settings", data={"ua_contact": "", "locale": ""},
                       follow_redirects=True)
    assert b"need the contact" in resp.data
    with papp.app_context():
        assert Setting.get("plugins.ua_contact") == "it@example.com"


def test_global_settings_validation(padmin, papp, listener):
    for bad in ({"ua_contact": "me (x); evil", "locale": ""},
                {"ua_contact": "", "locale": "not a locale!"}):
        padmin.post("/plugins/settings", data=bad)
    with papp.app_context():
        assert not Setting.get("plugins.ua_contact") and not Setting.get("plugins.locale")
    padmin.post("/plugins/settings", data={"ua_contact": "https://example.com/contact",
                                           "locale": "nl-NL"})
    assert ("manifest", {}) in _drain(listener)
    assert _audit(papp, "plugin.settings") == [{"changed": ["plugins.ua_contact",
                                                            "plugins.locale"]}]


def test_secret_status_without_the_value(padmin, papp):
    html = padmin.get("/plugins/").data.decode()
    assert "AISIGNX_ECHO_TOKEN" in html and "missing" in html
    papp.config["AISIGNX_ECHO_TOKEN"] = "sup3r-secret-value"
    html = padmin.get("/plugins/").data.decode()
    assert "configured" in html and "sup3r-secret-value" not in html
    media_id = add_item(papp)
    assert "sup3r-secret-value" not in padmin.get(f"/plugins/items/{media_id}/edit").get_data(
        as_text=True)
    body = padmin.get(f"/plugins/items/{media_id}/data").get_data(as_text=True)
    assert "sup3r-secret-value" not in body and '"has_token":true' in body.replace(" ", "")
    with papp.app_context():
        rows = AuditLog.query.all()
        assert all("sup3r-secret-value" not in (r.detail or "") for r in rows)


def test_cache_clear_is_audited(padmin, papp):
    media_id = add_item(papp)
    padmin.get(f"/plugins/items/{media_id}/data")
    assert padmin.post("/plugins/cache/clear").status_code == 303
    detail, = _audit(papp, "plugin.cache_clear")
    assert detail["files"] >= 2
    padmin.get(f"/plugins/items/{media_id}/data")
    assert len(echo_hooks(papp).CALLS) == 2


# --- geocoding --------------------------------------------------------------------------

def test_geocode_roles_and_limits(papp, pclient, monkeypatch):
    make_user(papp, "admin", "admin")
    make_user(papp, "viewer", "viewer")
    make_user(papp, "ed", "editor")
    calls = []
    monkeypatch.setattr(geo, "_open_meteo", lambda q: calls.append(q) or [
        {"label": f"{q} {i}", "lat": 1.0, "lon": 2.0} for i in range(8)])
    login(pclient, "viewer")
    assert pclient.get("/plugins/geocode?q=Paris").status_code == 403
    pclient.post("/logout")
    login(pclient, "ed")
    resp = pclient.get("/plugins/geocode?q=Paris")
    assert resp.status_code == 200 and len(resp.get_json()["results"]) == 5
    assert pclient.get("/plugins/geocode?q=" + "x" * 101).status_code == 400
    assert pclient.get("/plugins/geocode?q=").status_code == 400
    latlon = pclient.get("/plugins/geocode?q=52.1,5.2").get_json()["results"]
    assert latlon == [{"label": "52.1, 5.2", "lat": 52.1, "lon": 5.2}]
    statuses = [pclient.get(f"/plugins/geocode?q=Town{i}").status_code for i in range(20)]
    assert statuses.count(429) >= 1 and statuses[-1] == 429
    assert pclient.get("/plugins/geocode?q=Paris").headers["Retry-After"] == "60"


def test_geocode_upstream_error_is_a_code(padmin, monkeypatch):
    def fail(q):
        raise FetchError("http_503")

    monkeypatch.setattr(geo, "_open_meteo", fail)
    resp = padmin.get("/plugins/geocode?q=Nowhere")
    assert resp.status_code == 502 and resp.get_json()["error"] == "http_503"


class _FakeClient:
    def __init__(self, body, urls):
        self.body, self.urls = body, urls

    def get(self, url, **kw):
        self.urls.append((url, kw))
        return Response(200, {}, json.dumps(self.body).encode())


def test_postal_codes_use_nominatim_once_per_second(papp, monkeypatch):
    urls = []
    rows = [{"lat": "52.3702", "lon": "4.8952", "display_name": "1012, Amsterdam"}]
    monkeypatch.setattr(geo, "_client", lambda: _FakeClient(rows, urls))
    with papp.app_context():
        found = geo.geocode("1012 AB, nl")
        assert found == [{"label": "1012, Amsterdam", "lat": 52.3702, "lon": 4.8952}]
        assert "nominatim.openstreetmap.org" in urls[0][0] and "countrycodes=nl" in urls[0][0]
        assert geo.geocode("1012 AB, nl") == found  # cached: no second request
        assert len(urls) == 1
        with pytest.raises(RateLimited):
            geo.geocode("90210")


def test_open_meteo_key_goes_to_the_customer_host_redacted(papp, monkeypatch):
    urls = []
    body = {"results": [{"name": "Utrecht", "admin1": "Utrecht", "country": "Netherlands",
                         "latitude": 52.09083, "longitude": 5.12222}]}
    monkeypatch.setattr(geo, "_client", lambda: _FakeClient(body, urls))
    papp.config["AISIGNX_OPENMETEO_KEY"] = "om-key"
    with papp.app_context():
        found = geo.geocode("Utrecht")
    assert found == [{"label": "Utrecht, Utrecht, Netherlands", "lat": 52.0908, "lon": 5.1222}]
    url, kw = urls[0]
    assert url.startswith("https://customer-geocoding-api.open-meteo.com/") and kw["redact"] == ["apikey"]


def test_save_geocodes_a_typed_place(padmin, papp, monkeypatch):
    monkeypatch.setattr(geo, "_open_meteo",
                        lambda q: [{"label": "Delft, NL", "lat": 52.0116, "lon": 4.3571}])
    data = {"name": "E", "message": "Hi", "place.q": "Delft", "place.lat": "", "place.lon": "",
            "place.label": "", "count": "3", "color": "red"}
    assert padmin.post("/plugins/new/echo", data=data).status_code == 303
    with papp.app_context():
        from aisignx.models import Media

        place = Media.query.one().plugin_config["place"]
    assert place == {"q": "Delft", "lat": 52.0116, "lon": 4.3571, "label": "Delft, NL"}
