"""Plugin registry: discovery, plugin.json validation, failing start-up on bad plugins."""
import json
from pathlib import Path

import pytest

from aisignx.plugins import registry
from aisignx.plugins.registry import PluginLoadError, load_plugin

from .plugin_fixtures import FIXTURES, make_plugin_app, papp  # noqa: F401

BUNDLED = Path(registry.__file__).parent / "bundled"


def _plugin(tmp_path, data: dict, files=("index.html",), raw: bytes | None = None,
            hooks: str | None = None):
    """Write a plugin folder (key from data) under tmp_path/plugins; returns its folder."""
    key = data.get("key", "demo")
    folder = tmp_path / "plugins" / key
    (folder / "static").mkdir(parents=True, exist_ok=True)
    for name in files:
        path = folder / "static" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    payload = raw if raw is not None else json.dumps(data).encode("utf-8")
    (folder / "plugin.json").write_bytes(payload)
    if hooks is not None:
        (folder / "__init__.py").write_text(hooks, encoding="utf-8")
    return folder


def _load(folder):
    return load_plugin(folder, folder.parent / "unused-static", bundled=False)


BASE = {"key": "demo", "name": "Demo", "version": "1.0", "render": "frame",
        "files": ["index.html"]}


def test_discovers_fixture_plugins(papp):
    reg = papp.extensions["aisignx_plugins"]
    assert {"echo", "echo_native"} <= set(reg.plugins)
    echo = reg.get("echo")
    assert echo.render == "frame" and echo.data.ttl == 60 and echo.data.hosts == ("echo.example.com",)
    assert echo.hook("fetch") is not None and echo.hook("manifest_extra") is None
    assert echo.static_files() == ["player/plugins/echo/index.html", "player/plugins/echo/main.js"]
    native = reg.get("echo_native")
    assert native.render == "native" and native.player_script == "native.js"
    assert native.static_files() == ["player/plugins/echo_native/native.js",
                                     "player/plugins/echo_native/native.css"]


def test_bundled_plugins_load_and_are_bom_free(tmp_path, monkeypatch):
    app = make_plugin_app(tmp_path, monkeypatch, PLUGIN_DIRS=[])
    reg = app.extensions["aisignx_plugins"]
    bundled = sorted(p.parent.name for p in BUNDLED.glob("*/plugin.json"))
    assert sorted(reg.plugins) == bundled
    for path in BUNDLED.glob("*/plugin.json"):
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf"), path


def test_each_app_has_its_own_registry(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = make_plugin_app(tmp_path / "a", monkeypatch)
    b = make_plugin_app(tmp_path / "b", monkeypatch, PLUGIN_DIRS=[])
    assert "echo" in a.extensions["aisignx_plugins"].plugins
    assert "echo" not in b.extensions["aisignx_plugins"].plugins


def test_valid_minimal_plugin(tmp_path):
    plugin = _load(_plugin(tmp_path, BASE))
    assert plugin.key == "demo" and plugin.default_duration == 10 and plugin.data is None


@pytest.mark.parametrize("key", ["Demo", "d", "1demo", "demo-x", "a" * 33, None])
def test_rejects_bad_key(tmp_path, key):
    folder = _plugin(tmp_path, {**BASE, "key": "demo"})
    (folder / "plugin.json").write_text(json.dumps({**BASE, "key": key}), encoding="utf-8")
    with pytest.raises(PluginLoadError):
        _load(folder)


def test_rejects_key_not_matching_folder(tmp_path):
    folder = _plugin(tmp_path, BASE)
    (folder / "plugin.json").write_text(json.dumps({**BASE, "key": "other"}), encoding="utf-8")
    with pytest.raises(PluginLoadError, match="folder name"):
        _load(folder)


def test_rejects_byte_order_mark(tmp_path):
    folder = _plugin(tmp_path, BASE, raw=b"\xef\xbb\xbf" + json.dumps(BASE).encode())
    with pytest.raises(PluginLoadError, match="byte-order mark"):
        _load(folder)


def test_rejects_missing_or_bad_files(tmp_path):
    with pytest.raises(PluginLoadError, match="missing"):
        _load(_plugin(tmp_path, {**BASE, "files": ["index.html", "main.js"]}))
    for bad in ("../secret.js", "Main.JS", "/abs.js", "a b.js"):
        with pytest.raises(PluginLoadError):
            _load(_plugin(tmp_path, {**BASE, "files": ["index.html", bad]}))


def test_rejects_reserved_field_names(tmp_path):
    for name in ("name", "duration", "loop", "video_duration", "enabled"):
        data = {**BASE, "schema": [{"name": name, "type": "string"}]}
        with pytest.raises(PluginLoadError, match="reserved"):
            _load(_plugin(tmp_path, data))


@pytest.mark.parametrize("host", ["*.example.com", "10.0.0.1", "[::1]", "example.com:8080",
                                  "Example.com", "localhost", ""])
def test_rejects_wildcard_ip_or_port_hosts(tmp_path, host):
    data = {**BASE, "data": {"hosts": [host]}}
    with pytest.raises(PluginLoadError):
        _load(_plugin(tmp_path, data, hooks="def fetch(config, ctx):\n    return {}\n"))


def test_native_rules(tmp_path):
    native = {**BASE, "render": "native", "files": [], "player_script": "native.js"}
    plugin = _load(_plugin(tmp_path, native, files=("native.js",)))
    assert plugin.render == "native"
    with pytest.raises(PluginLoadError, match="cannot declare data"):
        _load(_plugin(tmp_path, {**native, "data": {"hosts": []}}, files=("native.js",),
                      hooks="def fetch(config, ctx):\n    return {}\n"))
    with pytest.raises(PluginLoadError, match="needs a player_script"):
        _load(_plugin(tmp_path, {**native, "player_script": None}, files=("native.js",)))


def test_frame_needs_index_html(tmp_path):
    with pytest.raises(PluginLoadError, match="index.html"):
        _load(_plugin(tmp_path, {**BASE, "files": ["main.js"]}, files=("main.js",)))
    with pytest.raises(PluginLoadError, match="native plugins only"):
        _load(_plugin(tmp_path, {**BASE, "player_script": "index.html"}))


def test_data_needs_a_fetch_hook_and_the_other_way_round(tmp_path):
    with pytest.raises(PluginLoadError, match="fetch"):
        _load(_plugin(tmp_path, {**BASE, "data": {"hosts": ["api.example.com"]}}))
    with pytest.raises(PluginLoadError, match="data section"):
        _load(_plugin(tmp_path, BASE, hooks="def fetch(config, ctx):\n    return {}\n"))


def test_data_keys_and_rates_must_match(tmp_path):
    hooks = "def fetch(config, ctx):\n    return {}\n"
    with pytest.raises(PluginLoadError, match="unknown field"):
        _load(_plugin(tmp_path, {**BASE, "data": {"keys": ["nope"]}}, hooks=hooks))
    with pytest.raises(PluginLoadError, match="not in data.hosts"):
        _load(_plugin(tmp_path, {**BASE, "data": {"hosts": ["a.example.com"],
                                                  "rate": {"b.example.com": 5}}}, hooks=hooks))


def test_broken_hooks_fail_loading(tmp_path):
    with pytest.raises(PluginLoadError, match="could not import hooks"):
        _load(_plugin(tmp_path, BASE, hooks="raise RuntimeError('boom')\n"))


def test_bad_secret_and_setting_key_length(tmp_path):
    with pytest.raises(PluginLoadError, match="secret"):
        _load(_plugin(tmp_path, {**BASE, "secrets": [{"name": "k", "env": "HOME"}]}))
    long_name = "s" * 32
    data = {**BASE, "key": "d" * 30, "settings": [{"name": long_name, "type": "string"}]}
    with pytest.raises(PluginLoadError, match="64"):
        _load(_plugin(tmp_path, data))


def test_bad_plugin_fails_app_start(tmp_path, monkeypatch):
    folder = _plugin(tmp_path, {**BASE, "files": ["index.html", "gone.js"]})
    with pytest.raises(PluginLoadError):
        make_plugin_app(tmp_path, monkeypatch, PLUGIN_DIRS=[str(folder.parent)])


def test_duplicate_keys_fail(tmp_path, monkeypatch):
    with pytest.raises(PluginLoadError, match="duplicate"):
        make_plugin_app(tmp_path, monkeypatch, PLUGIN_DIRS=[str(FIXTURES), str(FIXTURES)])


def test_plugin_dirs_is_never_read_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("PLUGIN_DIRS", str(FIXTURES))
    monkeypatch.setenv("AISIGNX_PLUGIN_DIRS", str(FIXTURES))
    app = make_plugin_app(tmp_path, monkeypatch, PLUGIN_DIRS=[])
    assert "echo" not in app.extensions["aisignx_plugins"].plugins
    from aisignx import create_app

    app2 = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite://"})
    assert app2.config["PLUGIN_DIRS"] == [] and app2.config["PLUGIN_FETCH_SYNC"] is False
