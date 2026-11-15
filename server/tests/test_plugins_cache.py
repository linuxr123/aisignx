"""Plugin data cache: TTL, stale-while-revalidate, single flight, warming, backoff, prune."""
import os
import threading
import time
import zipfile

import pytest

from aisignx.extensions import db
from aisignx.plugins import cache as cache_mod
from aisignx.plugins import service
from aisignx.plugins.cache import PluginCache, get_cache
from aisignx.plugins.fetch import RateLimited

from .conftest import run_job
from .plugin_fixtures import ECHO_CONFIG, echo_hooks, make_plugin_app, papp  # noqa: F401


@pytest.fixture
def async_app(tmp_path, monkeypatch):
    app = make_plugin_app(tmp_path, monkeypatch, PLUGIN_FETCH_SYNC=False)
    yield app
    app.extensions["aisignx_plugin_worker"].stop()
    with app.app_context():
        db.engine.dispose()


def _serve(app, config=None):
    with app.app_context():
        plugin = service.get_plugin("echo")
        return service.serve_data(plugin, dict(ECHO_CONFIG, **(config or {})))[0]


def _key(app, config=None):
    with app.app_context():
        return service.cache_key(service.get_plugin("echo"), dict(ECHO_CONFIG, **(config or {})))


def _expire(app, key, by=1):
    store = app.extensions["aisignx_plugin_cache"]
    entry = store.read(key)
    entry["expires_at"] = time.time() - by
    store._write_json(store._path(key), entry)


def test_fresh_entry_is_served_without_fetching(papp):
    hooks = echo_hooks(papp)
    first = _serve(papp)
    assert first.status == "ok" and first.entry["data"]["message"] == "Hello"
    assert len(first.entry["assets"]) == 1 and first.entry["ttl"] == 60
    second = _serve(papp)
    assert second.entry["data"] == first.entry["data"] and len(hooks.CALLS) == 1


def test_refresh_field_overrides_ttl_but_not_below_min(papp):
    assert _serve(papp, {"refresh": 600}).entry["ttl"] == 600
    with papp.app_context():
        plugin = service.get_plugin("echo")
        assert service.refresh_seconds(plugin, {"refresh": 5}) == 10  # min_ttl
        assert service.refresh_seconds(plugin, {}) == 60


def test_stale_while_revalidate_answers_at_once_and_refreshes_once(async_app):
    hooks = echo_hooks(async_app)
    key = _key(async_app)
    _serve(async_app)  # cold: queues the fetch and waits up to 2 s for it
    for _ in range(50):
        if async_app.extensions["aisignx_plugin_cache"].read(key):
            break
        time.sleep(0.05)
    _expire(async_app, key)
    hooks.FETCH_MODE["sleep"] = 0.6
    started = time.monotonic()
    results = [_serve(async_app) for _ in range(5)]
    assert time.monotonic() - started < 0.5
    assert all(r.status == "ok" and r.entry["data"]["n"] == 1 for r in results)
    assert not results[0].stale  # past expiry but within the grace period
    time.sleep(1.0)
    assert len(hooks.CALLS) == 2  # one refresh, not five
    assert _serve(async_app).entry["data"]["n"] == 2


def test_stale_flag_after_grace(papp):
    key = _key(papp)
    _serve(papp)
    echo_hooks(papp).FETCH_MODE["mode"] = "fail"
    _expire(papp, key, by=61)  # past expires_at + ttl
    served = _serve(papp)
    assert served.status == "ok" and served.stale


def test_single_flight_under_threads(async_app):
    hooks = echo_hooks(async_app)
    hooks.FETCH_MODE["sleep"] = 0.5
    out = []
    threads = [threading.Thread(target=lambda: out.append(_serve(async_app))) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(hooks.CALLS) == 1
    assert all(r.status == "ok" for r in out)


def test_cold_miss_answers_warming_within_two_seconds(async_app):
    echo_hooks(async_app).FETCH_MODE["sleep"] = 3.0
    started = time.monotonic()
    served = _serve(async_app)
    elapsed = time.monotonic() - started
    assert served.status == "warming" and served.retry_after == 5
    assert 1.5 < elapsed < 2.8


def test_failure_backs_off_and_keeps_the_last_good_copy(papp):
    hooks = echo_hooks(papp)
    key = _key(papp)
    _serve(papp)
    hooks.FETCH_MODE["mode"] = "fail"
    _expire(papp, key)
    served = _serve(papp)  # serves the copy; the inline refresh fails
    assert served.status == "ok" and served.entry["data"]["n"] == 1
    store = papp.extensions["aisignx_plugin_cache"]
    entry = store.read(key)
    assert entry["last_error_code"] == "http_503" and entry["fail_count"] == 1
    assert 55 < entry["backoff_until"] - time.time() <= 60
    calls = len(hooks.CALLS)
    _serve(papp)
    assert len(hooks.CALLS) == calls  # backing off: no new fetch
    entry["backoff_until"] = 0
    store._write_json(store._path(key), entry)
    store._backoff.clear()
    _serve(papp)
    assert store.read(key)["fail_count"] == 2
    assert 115 < store.read(key)["backoff_until"] - time.time() <= 120


def test_cold_failure_answers_upstream_unavailable(papp):
    hooks = echo_hooks(papp)
    hooks.FETCH_MODE["mode"] = "crash"
    served = _serve(papp)
    assert served.status == "upstream_unavailable" and 55 <= served.retry_after <= 60
    assert len(hooks.CALLS) == 1
    assert _serve(papp).status == "upstream_unavailable" and len(hooks.CALLS) == 1


def test_backoff_doubles_up_to_fifteen_minutes(tmp_path):
    store = PluginCache(tmp_path, 10)
    waits = []
    for _ in range(7):
        waits.append(round(store.store_failure("k", "timeout") - time.time()))
    assert waits == [60, 120, 240, 480, 900, 900, 900]


def test_rate_limited_upstream_serves_stale(papp, monkeypatch):
    hooks = echo_hooks(papp)
    key = _key(papp)
    _serve(papp)
    _expire(papp, key)

    def limited(config, ctx):
        raise RateLimited()

    monkeypatch.setattr(hooks, "fetch", limited)
    served = _serve(papp)
    assert served.status == "ok" and served.entry["data"]["n"] == 1
    assert papp.extensions["aisignx_plugin_cache"].read(key)["last_error_code"] == "rate_limited"


def test_survives_a_restart(tmp_path, monkeypatch):
    first = make_plugin_app(tmp_path, monkeypatch)
    _serve(first)
    second = make_plugin_app(tmp_path, monkeypatch)
    served = _serve(second)
    assert served.entry["data"]["n"] == 1 and echo_hooks(second).CALLS == []


def test_key_sharing(papp):
    a = _key(papp, {"place": {"q": "a", "lat": 52.3712, "lon": 4.8961, "label": "A"},
                    "note": "one", "count": 9})
    b = _key(papp, {"place": {"q": "b", "lat": 52.3698, "lon": 4.9012, "label": "B"},
                    "note": "two"})
    c = _key(papp, {"place": {"q": "c", "lat": 52.4, "lon": 4.9, "label": "C"}})
    d = _key(papp, {"message": "Other"})
    assert a == b and a != c and d != _key(papp)


def test_ctx_cache_is_shared_and_falls_back_to_old_values(papp):
    with papp.app_context():
        ctx = service.Context(service.get_plugin("echo"))
        calls = []
        assert ctx.cache("AAPL", 60, lambda: calls.append(1) or 1) == 1
        assert ctx.cache("AAPL", 60, lambda: calls.append(1) or 2) == 1
        assert len(calls) == 1
        key = service._digest(["echo", "1.0.0", "sub", "AAPL"])
        store = get_cache()
        store.sub_put(key, 1, 1)
        entry = store._read_json(store._path(key, sub=True))
        entry["expires_at"] = 0
        store._write_json(store._path(key, sub=True), entry)

        def boom():
            raise RuntimeError("down")

        assert ctx.cache("AAPL", 60, boom) == 1
        with pytest.raises(RuntimeError):
            ctx.cache("MSFT", 60, boom)


def test_prune(tmp_path):
    store = PluginCache(tmp_path, 1)
    old = store.store_success("old", {"x": 1}, [("a" * 32, "image/png", b"1" * 10)], 60)
    store.store_success("new", {"x": 2}, [("b" * 32, "image/png", b"2" * 10)], 60)
    old["last_read_at"] = time.time() - 8 * 86400
    store._write_json(store._path("old"), old)
    (store.assets_dir / ("c" * 32)).write_bytes(b"orphan")
    stale_tmp = tmp_path / ".x.json.abcd.tmp"
    stale_tmp.write_text("{}")
    os.utime(stale_tmp, (time.time() - 7200, time.time() - 7200))
    store.prune()
    assert store.read("old") is None and store.read("new") is not None
    assert sorted(p.name for p in store.assets_dir.iterdir()) == ["b" * 32]
    assert not stale_tmp.exists()


def test_prune_caps_size_least_recently_read_first(tmp_path):
    store = PluginCache(tmp_path, 1)  # 1 MB
    big = b"x" * (400 * 1024)
    for i, name in enumerate(("k1", "k2", "k3")):
        entry = store.store_success(name, {}, [(f"{i:032x}", "image/png", big + bytes([i]))], 60)
        entry["last_read_at"] = time.time() - 100 + i
        store._write_json(store._path(name), entry)
    store.prune()
    assert store.read("k1") is None and store.read("k3") is not None
    assert store.size_bytes() <= 1024 * 1024


def test_prune_job_and_clear(papp):
    _serve(papp)
    assert "removed" in run_job(papp, "plugin_cache_prune")
    with papp.app_context():
        assert get_cache().size_bytes() > 0
        assert get_cache().clear() >= 2
        assert get_cache().size_bytes() == 0


def test_backup_never_contains_the_plugin_cache(papp):
    from aisignx.backup import service as backup

    _serve(papp)
    assert os.listdir(papp.config["PLUGIN_CACHE_DIR"])
    with papp.app_context():
        path = backup.create("manual", include_uploads=True)
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
    assert "manifest.json" in names and not any("plugin-cache" in n for n in names)
    assert all(n == "manifest.json" or n.startswith(("db/", "instance/", "uploads/media/",
                                                     "uploads/thumbs/")) for n in names)


def test_cache_dir_default_is_beside_uploads(tmp_path, monkeypatch):
    monkeypatch.delenv("AISIGNX_PLUGIN_CACHE_DIR", raising=False)
    from aisignx.config import build_config

    (tmp_path / "instance").mkdir()
    config = build_config(str(tmp_path / "instance"))
    assert config["PLUGIN_CACHE_DIR"] == str(tmp_path / "plugin-cache")
    assert config["PLUGIN_CACHE_MB"] == 500 and cache_mod.WAIT_S == 2.0
