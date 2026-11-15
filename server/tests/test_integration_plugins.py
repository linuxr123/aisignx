"""All seven bundled plugins together (P8): one playlist with every plugin, the enable and
disable round trip through the admin, and backup and restore of plugin items.

No test here touches the network: only the manifest is read, never the data endpoints
(each plugin's own test module covers its fetcher against fixtures).
"""
import os
import zipfile

import pytest

from aisignx.backup import restore as restore_mod
from aisignx.backup import service as backup
from aisignx.extensions import db
from aisignx.models import Media, PlaylistItem
from aisignx.plugins import schema
from aisignx.plugins import service

from .conftest import DISPLAY_TOKEN, bearer, login, make_user, png_bytes, upload
from .plugin_fixtures import make_plugin_app, paired_with

API = "/api/player/v1"
BUNDLED = ("clock", "weather", "weatherradar", "rss", "stocks", "youtube", "image_library")
NATIVE = {"youtube", "image_library"}
NO_DATA = {"clock", "youtube", "image_library"}
FINNHUB = "integration-finnhub-key-0123456789"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("AISIGNX_FINNHUB_KEY", raising=False)
    # Bundled plugins only: no echo fixtures, so the test sees exactly the shipped set.
    app = make_plugin_app(tmp_path, monkeypatch, PLUGIN_DIRS=[], AISIGNX_FINNHUB_KEY=FINNHUB)
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def admin(app):
    client = app.test_client()
    make_user(app, "admin", "admin")
    login(client, "admin")
    return client


def _required(image_id: int) -> dict:
    return {
        "weather": {"location": "59.9139,10.7522"},
        "weatherradar": {"location": "59.9139,10.7522"},
        "rss": {"url": "https://news.example.org/feed.xml"},
        "stocks": {"symbols": "AAPL,MSFT"},
        "youtube": {"source": "videos", "videos": "https://youtu.be/dQw4w9WgXcQ"},
        "image_library": {"source": "selection", "media_ids": [image_id]},
    }


def _create_all(app, image_id: int) -> dict:
    """One item per bundled plugin, built from the schema defaults. Returns {key: media id}."""
    ids = {}
    with app.test_request_context():
        for key in BUNDLED:
            plugin = service.get_plugin(key)
            raw = {**schema.defaults(plugin.schema), **_required(image_id).get(key, {})}
            cfg, errors = service.validate_config(plugin, raw)
            assert not errors, (key, errors)
            media = service.create_item(plugin, f"{plugin.name} item", None, cfg, None)
            db.session.flush()
            ids[key] = media.id
        db.session.commit()
    return ids


def _image(admin, app) -> int:
    upload(admin, ("photo.png", png_bytes()))
    with app.app_context():
        return Media.query.filter_by(media_type="image").one().id


def _manifest(client, token) -> dict:
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    assert resp.status_code == 200
    return resp.get_json()


def _by_key(manifest: dict) -> dict:
    return {i["plugin"]["key"]: i for i in manifest["items"] if i["type"] == "plugin"}


def _settings_form(app, key: str, enabled: bool) -> dict:
    """The admin settings form for a plugin, posted with its default settings."""
    with app.app_context():
        plugin = service.get_plugin(key)
        form = {"enabled": "1"} if enabled else {}
        for fld in plugin.settings:
            value = fld.get("default")
            if fld["type"] == "boolean":
                if value:
                    form[fld["name"]] = "1"
            else:
                form[fld["name"]] = "" if value is None else str(value)
    return form


def test_all_seven_plugins_play_in_one_playlist(app, admin):
    with app.app_context():
        assert set(app.extensions["aisignx_plugins"].plugins) == set(BUNDLED)
    image_id = _image(admin, app)
    ids = _create_all(app, image_id)
    token = paired_with(app, *ids.values())
    manifest = _manifest(app.test_client(), token)
    items = _by_key(manifest)
    assert set(items) == set(BUNDLED)
    assert [i["plugin"]["key"] for i in manifest["items"]] == list(BUNDLED)  # order kept
    for key, item in items.items():
        block = item["plugin"]
        assert item["media_id"] == ids[key] and "#" in item["url"]
        assert block["render"] == ("native" if key in NATIVE else "frame")
        if key in NATIVE:
            assert not item["url"].startswith("/static/player/plugins/")
        else:
            assert item["url"].startswith(f"/static/player/plugins/{key}/index.html#c=")
        if key in NO_DATA:
            assert not block.get("data")
        else:
            assert block["data"] == f"{API}/plugins/{ids[key]}/data"
    # Native specifics: the YouTube embed and the image library's file list for the shells.
    assert items["youtube"]["url"].startswith("https://www.youtube-nocookie.com/embed/")
    files = items["image_library"]["plugin"]["media"]
    assert [f["media_id"] for f in files] == [image_id]
    assert files[0]["url"].startswith(f"{API}/media/{image_id}/") and files[0]["sha256"]
    # The secret and the private feed URL never reach a screen.
    text = str(manifest)
    assert FINNHUB not in text and "news.example.org" not in text
    # Every frame runner and native script the manifest points at is on the player page.
    page = admin.get("/player/").get_data(as_text=True)
    for key in BUNDLED:
        assert f"/static/player/plugins/{key}/" in page, key


def test_enable_disable_round_trip(app, admin):
    ids = _create_all(app, _image(admin, app))
    token = paired_with(app, *ids.values())
    screen = app.test_client()
    version = _manifest(screen, token)["version"]
    for key in BUNDLED:
        assert admin.post(f"/plugins/{key}/settings",
                          data=_settings_form(app, key, False)).status_code == 303
    off = _manifest(screen, token)
    assert _by_key(off) == {} and off["version"] != version
    # Disabled plugins are hidden from "new" but their items stay in the library.
    assert b"/plugins/new/clock" not in admin.get("/plugins/new").data
    with app.app_context():
        assert Media.query.filter_by(media_type="plugin").count() == len(BUNDLED)
    for key in BUNDLED:
        admin.post(f"/plugins/{key}/settings", data=_settings_form(app, key, True))
    on = _manifest(screen, token)
    assert set(_by_key(on)) == set(BUNDLED) and on["version"] == version


def test_backup_and_restore_keep_plugin_items_without_the_cache(app, admin):
    image_id = _image(admin, app)
    ids = _create_all(app, image_id)
    paired_with(app, *ids.values())
    with app.app_context():
        configs = {m.id: dict(m.plugin_config) for m in Media.query.filter_by(media_type="plugin")}
    cache_dir = app.config["PLUGIN_CACHE_DIR"]
    os.makedirs(os.path.join(cache_dir, "assets"), exist_ok=True)
    with open(os.path.join(cache_dir, "deadbeef.json"), "w", encoding="utf-8") as fh:
        fh.write("{}")
    with app.app_context():
        path = backup.create("manual", include_uploads=True)
    with zipfile.ZipFile(path) as zf:
        assert not any("plugin-cache" in n or n.endswith("deadbeef.json") for n in zf.namelist())

    # Lose every plugin item after the backup, then restore.
    with app.app_context():
        for media in Media.query.filter_by(media_type="plugin"):
            PlaylistItem.query.filter_by(media_id=media.id).delete()
            db.session.delete(media)
        db.session.commit()
        restore_mod.restore(path, skip_snapshot=True)
    backup.wait(10)
    with app.app_context():
        db.session.remove()
        restored = {m.id: m for m in Media.query.filter_by(media_type="plugin")}
        assert {m.id: m.plugin_config for m in restored.values()} == configs
        assert {m.plugin_key for m in restored.values()} == set(BUNDLED)
        assert PlaylistItem.query.count() == len(BUNDLED)
    # The cache folder is left alone by a restore: it is not part of the backup.
    assert os.path.exists(os.path.join(cache_dir, "deadbeef.json"))
    # The restored items play again.
    assert set(_by_key(_manifest(app.test_client(), DISPLAY_TOKEN))) == set(BUNDLED)


def test_an_org_setting_change_moves_items_to_a_new_cache_entry(app):
    from aisignx.models import Setting

    with app.test_request_context():
        weather = service.get_plugin("weather")
        cfg, errors = service.validate_config(
            weather, {**schema.defaults(weather.schema), "location": "59.9139,10.7522"})
        assert not errors
        before = service.cache_key(weather, cfg)
        Setting.set("plugins.weather.provider", "met-norway")
        db.session.commit()
        assert service.cache_key(weather, cfg) != before
        Setting.set("plugins.weather.provider", "open-meteo")
        db.session.commit()
        assert service.cache_key(weather, cfg) == before
        # Plugins without settings keep their keys (no settings in the digest).
        clock = service.get_plugin("clock")
        assert clock.data is None and not clock.settings
