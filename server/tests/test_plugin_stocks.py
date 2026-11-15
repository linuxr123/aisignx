"""Stocks plugin (Finnhub): config, playability, the fetcher against recorded responses,
the per-symbol cache, the data endpoint, and the key never leaving the server.
No test touches the network: ctx.http is replaced by a fake that serves fixtures."""
import json
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aisignx.extensions import db
from aisignx.models import AuditLog
from aisignx.plugins import service
from aisignx.plugins.fetch import FetchError, Response

from .conftest import bearer, login, make_user
from .plugin_fixtures import add_item, make_plugin_app, paired_with

HERE = Path(__file__).resolve().parent
FIXTURE = json.loads((HERE / "fixtures" / "stocks" / "finnhub.json").read_text(encoding="utf-8"))
STATIC = HERE.parent / "aisignx" / "static" / "player" / "plugins" / "stocks"
BUNDLED = HERE.parent / "aisignx" / "plugins" / "bundled" / "stocks"
KEY = "fh-test-key-7b1c9e2d4f"
API = "/api/player/v1"


class FakeFinnhub:
    """Stands in for ctx.http: answers /quote and /stock/profile2 from the fixture."""

    def __init__(self):
        self.calls = []        # (path, symbol, headers)
        self.fail = None       # None, or a status code / "error" to fail every request

    def get(self, url, *, headers=None, **_kw):
        parts = urlsplit(url)
        assert parts.scheme == "https" and parts.hostname == "finnhub.io"
        path = parts.path.removeprefix("/api/v1/")
        symbol = parse_qs(parts.query)["symbol"][0]
        self.calls.append((path, symbol, dict(headers or {}), url))
        if self.fail == "error":
            raise FetchError("timeout")
        if self.fail:
            return Response(self.fail, {}, b'{"error":"upstream text with internal.host"}', url)
        table = FIXTURE["quote" if path == "quote" else "profile2"]
        body = table.get(symbol, {} if path != "quote" else FIXTURE["quote"]["NOPE"])
        return Response(200, {"content-type": "application/json"}, json.dumps(body).encode(), url)

    def count(self, path):
        return sum(1 for c in self.calls if c[0] == path)


@pytest.fixture
def fake(monkeypatch):
    fh = FakeFinnhub()
    monkeypatch.setattr(service.Context, "http", property(lambda self: fh))
    return fh


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("AISIGNX_FINNHUB_KEY", raising=False)
    app = make_plugin_app(tmp_path, monkeypatch, AISIGNX_FINNHUB_KEY=KEY)
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def _plugin(app):
    with app.app_context():
        return service.get_plugin("stocks")


def _validate(app, raw):
    with app.test_request_context():
        return service.validate_config(service.get_plugin("stocks"), raw)


def _fetch(app, config):
    with app.test_request_context():
        plugin = service.get_plugin("stocks")
        return plugin.hook("fetch")(dict(config), service.Context(plugin))


def _expire_all(app):
    store = app.extensions["aisignx_plugin_cache"]
    for path in store.root.rglob("*.json"):
        entry = json.loads(path.read_text())
        entry["expires_at"] = time.time() - 120
        path.write_text(json.dumps(entry))


# --- plugin.json, defaults, normalize ---------------------------------------------------

def test_plugin_json_loads_with_its_files(app):
    plugin = _plugin(app)
    assert plugin.render == "frame" and plugin.version == "3.0.0"
    assert set(plugin.files) == {"index.html", "main.js", "style.css"}
    assert all((STATIC / f).is_file() for f in plugin.files)
    assert list(plugin.data.hosts) == ["finnhub.io"] and list(plugin.data.keys) == ["symbols"]
    assert plugin.data.ttl == 60 and plugin.data.min_ttl == 60
    assert plugin.data.rate == {"finnhub.io": 30}
    assert [s["env"] for s in plugin.secrets] == ["AISIGNX_FINNHUB_KEY"]
    assert "Finnhub" in plugin.terms and "personal" in plugin.terms
    assert plugin.attribution == "Data: Finnhub"
    raw = (BUNDLED / "plugin.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    # Only the files the spec lists: no .vs folders, no markdown.
    assert sorted(p.name for p in STATIC.iterdir()) == ["index.html", "main.js", "style.css"]
    assert not [p for p in BUNDLED.rglob("*") if p.suffix == ".md" or ".vs" in p.parts]


def test_defaults(app):
    config, errors = _validate(app, {"symbols": "AAPL"})
    assert not errors
    assert config["mode"] == "grid" and config["speed"] == "normal" and config["theme"] == "dark"
    assert config["refresh"] == 60
    from aisignx.plugins import schema

    form = schema.defaults(_plugin(app).schema)   # what a new form starts with
    assert form["show_name"] is True and form["show_pct"] is True and form["mode"] == "grid"


def test_normalize_symbols(app):
    config, errors = _validate(app, {"symbols": " aapl, msft  brk.b,AAPL, binance:btcusdt "})
    assert not errors
    assert config["symbols"] == "AAPL,MSFT,BRK.B,BINANCE:BTCUSDT"
    again, errors = _validate(app, dict(config))  # idempotent
    assert not errors and again["symbols"] == config["symbols"]


@pytest.mark.parametrize("raw", ["", "   ", ",,", "TOOLONGSYMBOL12345", "AA$PL", "A B C D E F G H I J "
                                 "K L M N O P Q R S T U"])
def test_bad_symbols_are_refused(app, raw):
    _config, errors = _validate(app, {"symbols": raw})
    assert "symbols" in errors


def test_refresh_minimum(app):
    _config, errors = _validate(app, {"symbols": "AAPL", "refresh": 30})
    assert "refresh" in errors


# --- playable and secrets ---------------------------------------------------------------

def test_not_playable_without_key(app, client, fake):
    make_user(app, "admin", "admin")
    media_id = add_item(app, "stocks", {"symbols": "AAPL"})
    token = paired_with(app, media_id)
    items = client.get(f"{API}/manifest", headers=bearer(token)).get_json()["items"]
    assert [i["media_id"] for i in items] == [media_id]
    app.config["AISIGNX_FINNHUB_KEY"] = ""
    items = client.get(f"{API}/manifest", headers=bearer(token)).get_json()["items"]
    assert items == []
    with pytest.raises(FetchError) as exc:
        _fetch(app, {"symbols": "AAPL"})
    assert exc.value.code == "missing_secret"
    assert fake.calls == []


def test_key_is_a_header_and_never_in_any_output(app, client, fake):
    make_user(app, "admin", "admin")
    login(client, "admin")
    resp = client.post("/plugins/new/stocks", data={
        "name": "Markets", "symbols": "aapl, msft", "mode": "board", "theme": "dark",
        "speed": "normal", "show_name": "1", "show_pct": "1", "refresh": "60"})
    assert resp.status_code in (302, 303), resp.get_data(as_text=True)[:500]
    with app.app_context():
        from aisignx.models import Media
        media_id = Media.query.filter_by(plugin_key="stocks").one().id
    token = paired_with(app, media_id)
    outputs = [
        client.get(f"{API}/manifest", headers=bearer(token)).get_data(as_text=True),
        client.get(f"{API}/plugins/{media_id}/data", headers=bearer(token)).get_data(as_text=True),
        client.get("/plugins/").get_data(as_text=True),
        client.get(f"/plugins/items/{media_id}/edit").get_data(as_text=True),
        client.get(f"/plugins/items/{media_id}/data").get_data(as_text=True),
        client.get(f"/plugins/items/{media_id}/preview").get_data(as_text=True),
    ]
    assert fake.calls
    for path, _symbol, headers, url in fake.calls:
        assert headers["X-Finnhub-Token"] == KEY
        assert KEY not in url and "token" not in url.lower()
    for text in outputs:
        assert KEY not in text
    assert "AISIGNX_FINNHUB_KEY" in outputs[2] and "configured" in outputs[2]
    with app.app_context():
        assert all(KEY not in (r.detail or "") for r in AuditLog.query.all())
    store = app.extensions["aisignx_plugin_cache"]
    for path in store.root.rglob("*"):
        if path.is_file():
            assert KEY.encode() not in path.read_bytes()


# --- fetch ------------------------------------------------------------------------------

def test_fetch_data_shape(app, fake):
    result = _fetch(app, {"symbols": "AAPL,SAP,BINANCE:BTCUSDT,NOPE"})
    data = result.data
    assert set(data) == {"quotes", "as_of_ms", "attribution", "delayed"}
    assert data["attribution"] == "Data: Finnhub" and data["delayed"] is False
    by = {q["symbol"]: q for q in data["quotes"]}
    assert [q["symbol"] for q in data["quotes"]] == ["AAPL", "SAP", "BINANCE:BTCUSDT", "NOPE"]
    for q in data["quotes"]:
        assert set(q) == {"symbol", "name", "price", "change", "pct", "currency", "ts"}
    assert by["AAPL"] == {"symbol": "AAPL", "name": "Apple Inc", "price": 189.84, "change": 1.23,
                          "pct": 0.6522, "currency": "USD", "ts": 1731704400}
    assert by["SAP"]["currency"] == "EUR" and by["SAP"]["name"] == "SAP SE<b>"  # control chars gone
    assert by["BINANCE:BTCUSDT"]["name"] is None and by["BINANCE:BTCUSDT"]["currency"] is None
    assert by["NOPE"]["price"] is None and by["NOPE"]["ts"] is None
    assert data["as_of_ms"] == 1731706000 * 1000
    # Exchange-prefixed symbols have no company profile, so none is asked for.
    assert ("stock/profile2", "BINANCE:BTCUSDT") not in [(c[0], c[1]) for c in fake.calls]


def test_delayed_label_setting(app, fake):
    from aisignx.models import Setting

    with app.app_context():
        Setting.set("plugins.stocks.delayed_label", "1")
        db.session.commit()
    assert _fetch(app, {"symbols": "AAPL"}).data["delayed"] is True


def test_per_symbol_cache_is_shared(app, fake):
    _fetch(app, {"symbols": "AAPL,MSFT"})
    assert fake.count("quote") == 2 and fake.count("stock/profile2") == 2
    _fetch(app, {"symbols": "MSFT,AAPL,SAP"})   # another item: only SAP is new
    assert fake.count("quote") == 3 and fake.count("stock/profile2") == 3
    _expire_all(app)                              # quotes and profiles both expired
    _fetch(app, {"symbols": "AAPL"})
    assert fake.count("quote") == 4


def test_failing_symbol_keeps_its_last_quote(app, fake):
    _fetch(app, {"symbols": "AAPL,MSFT"})
    _expire_all(app)
    fake.fail = 503
    data = _fetch(app, {"symbols": "AAPL,MSFT"}).data   # every refresh failed: old values
    assert [q["price"] for q in data["quotes"]] == [189.84, 415.5]


def test_all_quotes_failing_raises_a_code(app, fake):
    fake.fail = 503
    with pytest.raises(FetchError) as exc:
        _fetch(app, {"symbols": "AAPL,MSFT"})
    assert exc.value.code == "http_503"
    fake.fail = "error"
    with pytest.raises(FetchError) as exc:
        _fetch(app, {"symbols": "AAPL"})
    assert exc.value.code == "timeout"


def test_bad_key_stops_the_fetch(app, fake):
    fake.fail = 401
    with pytest.raises(FetchError) as exc:
        _fetch(app, {"symbols": "AAPL,MSFT,SAP"})
    assert exc.value.code == "http_401"
    assert fake.count("quote") == 1   # no point asking for the others


def test_rate_limit_answer_is_rate_limited(app, fake):
    fake.fail = 429
    with pytest.raises(FetchError) as exc:
        _fetch(app, {"symbols": "AAPL"})
    assert exc.value.code == "rate_limited"


def test_bucket_is_30_per_minute(app):
    """The real client: finnhub.io gets 30 requests a minute, then RateLimited."""
    from aisignx.plugins.fetch import Buckets

    buckets = Buckets()
    assert sum(buckets.take("finnhub.io", 30) for _ in range(40)) == 30


# --- data endpoint: fresh, then stale on failure ----------------------------------------

def test_data_endpoint_and_stale_copy(app, client, fake):
    make_user(app, "admin", "admin")
    media_id = add_item(app, "stocks", {"symbols": "AAPL,MSFT", "mode": "ticker"})
    token = paired_with(app, media_id)
    resp = client.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    body = resp.get_json()
    assert resp.status_code == 200 and body["stale"] is False and body["refresh_s"] == 60
    assert [q["symbol"] for q in body["data"]["quotes"]] == ["AAPL", "MSFT"]
    _expire_all(app)  # past expires_at + grace (the ttl), so the copy counts as stale
    store = app.extensions["aisignx_plugin_cache"]
    for path in store.root.glob("sub-*.json"):  # no per-symbol fallback either
        path.unlink()
    fake.fail = 503
    resp = client.get(f"{API}/plugins/{media_id}/data", headers=bearer(token))
    body = resp.get_json()
    assert resp.status_code == 200 and body["stale"] is True and body["error"] == "http_503"
    assert body["data"]["quotes"][0]["price"] == 189.84
    assert "internal.host" not in resp.get_data(as_text=True)


def test_manifest_entry(app, client, fake):
    make_user(app, "admin", "admin")
    media_id = add_item(app, "stocks", {"symbols": "aapl", "refresh": 120})
    token = paired_with(app, media_id)
    item, = client.get(f"{API}/manifest", headers=bearer(token)).get_json()["items"]
    assert item["url"].startswith("/static/player/plugins/stocks/index.html#c=")
    plugin = item["plugin"]
    assert plugin["key"] == "stocks" and plugin["refresh_s"] == 120
    assert plugin["data"] == f"{API}/plugins/{media_id}/data"
    assert plugin["config"]["symbols"] == "AAPL"
    assert plugin["attribution"] == "Data: Finnhub"
    assert item["duration_ms"] == 15000


# --- front end (pure helpers, in Node) ---------------------------------------------------

@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_front_end_helpers():
    out = subprocess.run(["node", str(HERE / "js" / "plugin_stocks.js"), str(STATIC / "main.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr + out.stdout
    assert out.stdout.strip().endswith("ok")


def test_front_end_rules():
    js = (STATIC / "main.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "eval(" not in js and 'setAttribute("style"' not in js
    assert "setTimeout" not in js and "setInterval" not in js   # timers go through ctx
    assert "loops" not in js                                     # the player owns duration
    for name in ("main.js", "style.css", "index.html"):
        raw = (STATIC / name).read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf") and len(raw.decode("utf-8").splitlines()) < 800


def test_admin_page_warns_about_terms_and_a_missing_key(app, client):
    make_user(app, "admin", "admin")
    login(client, "admin")
    app.config["AISIGNX_FINNHUB_KEY"] = ""
    html = client.get("/plugins/").get_data(as_text=True)
    assert "AISIGNX_FINNHUB_KEY" in html and "missing" in html
    assert "personal, non-commercial use" in html and "exchange display licences" in html
    form = client.get("/plugins/new/stocks").get_data(as_text=True)
    assert "personal, non-commercial use" in form
