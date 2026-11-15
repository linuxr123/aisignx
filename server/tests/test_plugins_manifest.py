"""Plugin items in the player manifest: entry shapes, change detection, playability."""
import json

from aisignx.extensions import db
from aisignx.models import Media, Setting

from .conftest import bearer, make_user
from .plugin_fixtures import add_item, paired_with, papp, pclient  # noqa: F401

API = "/api/player/v1"
BASE_KEYS = {"id", "media_id", "type", "url", "mime", "sha256", "size", "width", "height",
             "duration_ms", "media_duration_ms", "transition", "fit", "muted"}


def _manifest(client, token):
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    assert resp.status_code == 200
    return resp.get_json()


def _set(app, key, value):
    with app.app_context():
        Setting.set(key, value)
        db.session.commit()


def test_frame_entry_shape(papp, pclient):
    make_user(papp, "admin", "admin")
    _set(papp, "timezone", "Europe/Amsterdam")
    _set(papp, "plugins.locale", "nl-NL")
    media_id = add_item(papp)
    token = paired_with(papp, media_id)
    item, = _manifest(pclient, token)["items"]
    assert set(item) == BASE_KEYS | {"plugin"}
    assert item["type"] == "plugin" and item["media_id"] == media_id
    assert item["url"].startswith("/static/player/plugins/echo/index.html#c=")
    assert "?" not in item["url"] and len(item["url"].split("#c=")[1]) == 12
    assert item["mime"] is None and item["sha256"] is None and item["duration_ms"] == 12000
    assert item["muted"] is True and item["media_duration_ms"] is None
    plugin = item["plugin"]
    assert plugin["key"] == "echo" and plugin["version"] == "1.0.0" and plugin["render"] == "frame"
    assert plugin["data"] == f"{API}/plugins/{media_id}/data"
    assert plugin["refresh_s"] == 60 and plugin["ends_itself"] is False
    assert plugin["locale"] == "nl-NL" and plugin["attribution"] == "Echo data: nobody"
    assert plugin["config"]["tz"] == "Europe/Amsterdam"  # "" resolved to the org time zone
    assert "note" not in plugin["config"]
    assert "keep-me-private" not in json.dumps(item)


def test_native_entry_shape(papp, pclient, monkeypatch):
    make_user(papp, "admin", "admin")
    papp.config["AISIGNX_ECHO_TOKEN"] = "tok-123-secret"
    media_id = add_item(papp, "echo_native", {"words": "hey", "mute": False, "seconds": 4})
    token = paired_with(papp, media_id)
    item, = _manifest(pclient, token)["items"]
    assert item["url"].startswith("https://www.youtube-nocookie.com/embed/abcdefghijk?autoplay=1#c=")
    assert item["muted"] is False
    plugin = item["plugin"]
    assert plugin["render"] == "native" and plugin["data"] is None and plugin["refresh_s"] is None
    assert plugin["ends_itself"] is True and plugin["words"] == "hey"
    assert "tok-123-secret" not in json.dumps(item)


def test_other_items_keep_their_key_set(papp, pclient):
    make_user(papp, "admin", "admin")
    with papp.app_context():
        web = Media(name="W", media_type="web", url="https://example.com", default_duration=5)
        db.session.add(web)
        db.session.commit()
        web_id = web.id
    token = paired_with(papp, web_id, add_item(papp))
    web_item, plugin_item = _manifest(pclient, token)["items"]
    assert set(web_item) == BASE_KEYS
    assert set(plugin_item) == BASE_KEYS | {"plugin"}


def test_config_change_changes_hash_and_version(papp, pclient):
    make_user(papp, "admin", "admin")
    media_id = add_item(papp)
    token = paired_with(papp, media_id)
    first = _manifest(pclient, token)
    with papp.app_context():
        media = db.session.get(Media, media_id)
        media.plugin_config = {**media.plugin_config, "message": "Changed"}
        db.session.commit()
    second = _manifest(pclient, token)
    assert first["version"] != second["version"]
    assert first["items"][0]["url"] != second["items"][0]["url"]
    with papp.app_context():  # a private field also changes the data, so the hash
        media = db.session.get(Media, media_id)
        media.plugin_config = {**media.plugin_config, "note": "other"}
        db.session.commit()
    third = _manifest(pclient, token)
    assert third["items"][0]["url"] == second["items"][0]["url"]  # public view unchanged


def test_disabled_or_invalid_items_are_dropped(papp, pclient):
    make_user(papp, "admin", "admin")
    good, bad = add_item(papp), add_item(papp, name="Bad")
    with papp.app_context():
        media = db.session.get(Media, bad)
        media.plugin_config = {**media.plugin_config, "count": 99}
        db.session.commit()
    token = paired_with(papp, good, bad)
    assert [i["media_id"] for i in _manifest(pclient, token)["items"]] == [good]
    _set(papp, "plugins.echo.enabled", "0")
    assert _manifest(pclient, token)["items"] == []
    _set(papp, "plugins.echo.enabled", "1")
    assert len(_manifest(pclient, token)["items"]) == 1


def test_playable_hook_and_missing_plugin(papp, pclient):
    make_user(papp, "admin", "admin")
    unplayable = add_item(papp, config={"message": "unplayable"})
    gone = add_item(papp)
    with papp.app_context():
        db.session.get(Media, gone).plugin_key = "uninstalled"
        db.session.commit()
    token = paired_with(papp, unplayable, gone)
    assert _manifest(pclient, token)["items"] == []


def test_failing_manifest_extra_drops_only_that_item(papp, pclient, monkeypatch):
    make_user(papp, "admin", "admin")
    native = add_item(papp, "echo_native", {"words": "x"})
    echo = add_item(papp)
    token = paired_with(papp, native, echo)
    hooks = papp.extensions["aisignx_plugins"].get("echo_native").hooks

    def broken(media, config, ctx):
        raise RuntimeError("bug")

    monkeypatch.setattr(hooks, "manifest_extra", broken)
    assert [i["media_id"] for i in _manifest(pclient, token)["items"]] == [echo]
