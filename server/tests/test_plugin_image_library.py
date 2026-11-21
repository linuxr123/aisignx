"""Image library plugin (P7): plugin.json, the save-time checks, auto duration, the manifest
block (deleted ids skipped, "newest" order), the admin preview, and
static/player/plugins/image_library/native.js run in Node (tests/js/plugin_image_library.js)."""
import json
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.models import Media
from aisignx.plugins import registry, service

from .conftest import bearer, login, make_user
from .plugin_fixtures import add_item, make_plugin_app, paired_with

ROOT = Path(__file__).resolve().parent
STATIC = ROOT.parent / "aisignx" / "static" / "player" / "plugins" / "image_library"
BUNDLED = ROOT.parent / "aisignx" / "plugins" / "bundled" / "image_library"
API = "/api/player/v1"
KEY = "image_library"
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Load only this bundled plugin, so the module does not depend on the others.
    folders = registry._plugin_folders
    monkeypatch.setattr(registry, "_plugin_folders", lambda root: [
        p for p in folders(root) if root != registry.BUNDLED_DIR or p.name == KEY])
    app = make_plugin_app(tmp_path, monkeypatch)
    make_user(app, "admin", "admin")
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def _images(app, count, start=0, kind="image"):
    """Image rows (no files needed: the manifest only builds URLs). Returns their ids;
    each is a minute newer than the one before."""
    ids = []
    with app.app_context():
        for i in range(start, start + count):
            media = Media(name=f"Photo {i + 1}", media_type=kind,
                          stored_name=f"img{i + 1:03d}.jpg" if kind != "web" else None,
                          url="https://example.com" if kind == "web" else None,
                          mime_type="image/jpeg", file_size=1000 + i, width=1920, height=1080,
                          checksum_sha256=f"{i:064x}", default_duration=10,
                          created_at=T0 + timedelta(minutes=i))
            db.session.add(media)
            db.session.flush()
            ids.append(media.id)
        db.session.commit()
    return ids


def _config(**overrides):
    return {"source": "selection", "media_ids": [], "image_source": "", "source_count": 50, "source_order": "newest",
            "newest_count": 10, "mode": "slideshow",
            "grid": "2x2", "seconds_per_image": 8, "transition": "fade", "fit": "contain",
            "background": "blur", "caption": "none", "shuffle": False, "auto_duration": True,
            **overrides}


def _validate(app, raw):
    with app.test_request_context():
        return service.validate_config(service.get_plugin(KEY), raw)


def _manifest(client, token):
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    assert resp.status_code == 200
    return resp.get_json()


def _duration(app, media_id):
    with app.app_context():
        return db.session.get(Media, media_id).default_duration


# --- plugin.json and files ----------------------------------------------------------------

def test_plugin_json_loads(app):
    plugin = app.extensions["aisignx_plugins"].get(KEY)
    assert plugin.render == "native" and plugin.data is None and plugin.ends_itself is False
    assert plugin.player_script == "native.js" and plugin.player_style == "native.css"
    assert {f["name"] for f in plugin.schema} == {
        "source", "media_ids", "image_source", "source_count", "source_order", "newest_count", "mode", "grid", "seconds_per_image",
        "transition", "fit", "caption", "background", "shuffle", "auto_duration"}
    media_ids = next(f for f in plugin.schema if f["name"] == "media_ids")
    assert media_ids["type"] == "media_multi" and media_ids["max"] == 200
    for name in ("source", "mode", "fit"):
        assert not next(f for f in plugin.schema if f["name"] == name)["private"]


def test_files_are_utf8_without_bom_and_small():
    for path in [BUNDLED / "plugin.json", BUNDLED / "__init__.py",
                 STATIC / "native.js", STATIC / "native.css"]:
        raw = path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf"), path
        assert len(raw.decode("utf-8").splitlines()) < 800, path


def test_native_code_follows_the_player_rules():
    js = (STATIC / "native.js").read_text(encoding="utf-8")
    css = (STATIC / "native.css").read_text(encoding="utf-8")
    assert ".innerHTML" not in js and "eval(" not in js and 'setAttribute("style"' not in js
    assert "fetch(" not in js and "XMLHttpRequest" not in js
    classes = set(re.findall(r"^\.([a-z0-9-]+)", css, flags=re.MULTILINE))
    assert classes and all(c.startswith("pl-imglib-") for c in classes)


def test_defaults(app):
    plugin = app.extensions["aisignx_plugins"].get(KEY)
    from aisignx.plugins import schema

    defaults = schema.defaults(plugin.schema)
    assert defaults == _config()


# --- validation ---------------------------------------------------------------------------

def test_selection_needs_an_image(app):
    _, errors = _validate(app, _config())
    assert "media_ids" in errors
    ids = _images(app, 2)
    config, errors = _validate(app, _config(media_ids=[str(ids[1]), str(ids[0])]))
    assert not errors and config["media_ids"] == [ids[1], ids[0]]


def test_newest_needs_no_selection(app):
    config, errors = _validate(app, _config(source="newest", newest_count="5"))
    assert not errors and config["newest_count"] == 5


def test_refuses_non_images_and_bad_values(app):
    web, = _images(app, 1, kind="web")
    _, errors = _validate(app, _config(media_ids=[web]))
    assert "media_ids" in errors
    ids = _images(app, 1, start=5)
    _, errors = _validate(app, _config(media_ids=ids, seconds_per_image=2, grid="4x4",
                                       mode="spin", newest_count=101))
    assert {"seconds_per_image", "grid", "mode", "newest_count"} <= set(errors)


# --- auto duration ------------------------------------------------------------------------

def test_auto_duration(app):
    ids = _images(app, 5)
    assert _duration(app, add_item(app, KEY, _config(media_ids=ids, seconds_per_image=6))) == 30
    # Collage: every image once, and every tile changes at least once.
    assert _duration(app, add_item(app, KEY, _config(media_ids=ids, mode="collage"))) == 40
    assert _duration(app, add_item(app, KEY, _config(media_ids=ids[:2], mode="collage",
                                                     grid="3x3", seconds_per_image=5))) == 45
    assert _duration(app, add_item(app, KEY, _config(source="newest", newest_count=12,
                                                     seconds_per_image=10))) == 120


def test_auto_duration_off_uses_the_form(app):
    ids = _images(app, 3)
    assert _duration(app, add_item(app, KEY, _config(media_ids=ids, auto_duration=False),
                                   duration=25)) == 25
    assert _duration(app, add_item(app, KEY, _config(media_ids=ids, auto_duration=False))) == 40


def test_editor_creates_from_the_form(app, client):
    ids = _images(app, 3)
    make_user(app, "ed", "editor")
    login(client, "ed")
    page = client.get(f"/plugins/new/{KEY}")
    assert page.status_code == 200 and b"data-media-multi" in page.data
    assert b"Photo 1" in page.data
    form = {"name": "Lobby photos", "duration": "", "source": "selection", "newest_count": "10",
            "mode": "kenburns", "grid": "2x2", "seconds_per_image": "7", "transition": "fade",
            "fit": "cover", "background": "blur", "caption": "name", "auto_duration": "1"}
    bad = client.post(f"/plugins/new/{KEY}", data=form)
    assert bad.status_code == 400 and b"Choose at least one image." in bad.data
    resp = client.post(f"/plugins/new/{KEY}", data={**form, "media_ids": [str(i) for i in ids]})
    assert resp.status_code == 303
    with app.app_context():
        media = Media.query.filter_by(plugin_key=KEY).one()
        assert media.plugin_config["media_ids"] == ids and media.default_duration == 21
        assert media.plugin_config["mode"] == "kenburns"


# --- manifest -----------------------------------------------------------------------------

def test_manifest_entry(app, client):
    ids = _images(app, 3)
    media_id = add_item(app, KEY, _config(media_ids=[ids[2], ids[0]], caption="name"))
    token = paired_with(app, media_id)
    item, = _manifest(client, token)["items"]
    assert item["type"] == "plugin" and item["muted"] is True
    assert item["url"].startswith("about:blank#aisignx-plugin=image_library&c=")
    plugin = item["plugin"]
    assert plugin["render"] == "native" and plugin["data"] is None
    assert plugin["config"]["mode"] == "slideshow" and plugin["config"]["fit"] == "contain"
    first, second = plugin["media"]
    assert first["media_id"] == ids[2] and second["media_id"] == ids[0]
    assert first["url"] == f"{API}/media/{ids[2]}/img003.jpg"
    assert first["name"] == "Photo 3" and first["mime"] == "image/jpeg"
    assert {"url", "mime", "sha256", "size", "width", "height", "media_id", "name"} == set(first)
    # The file URL works for the display (no file on disk here, so 404 not 401).
    assert client.get(first["url"], headers=bearer(token)).status_code == 404
    page = client.get("/player/")
    assert b"player/plugins/image_library/native.js?v=" in page.data
    assert b"player/plugins/image_library/native.css?v=" in page.data


def test_names_only_with_captions(app, client):
    ids = _images(app, 1)
    token = paired_with(app, add_item(app, KEY, _config(media_ids=ids)))
    entry, = _manifest(client, token)["items"][0]["plugin"]["media"]
    assert "name" not in entry and "Photo 1" not in json.dumps(_manifest(client, token))


def test_deleted_ids_are_skipped(app, client):
    ids = _images(app, 3)
    media_id = add_item(app, KEY, _config(media_ids=ids))
    token = paired_with(app, media_id)
    before = _manifest(client, token)
    with app.app_context():
        db.session.delete(db.session.get(Media, ids[1]))
        db.session.commit()
    after = _manifest(client, token)
    assert [m["media_id"] for m in after["items"][0]["plugin"]["media"]] == [ids[0], ids[2]]
    assert after["version"] != before["version"]
    assert after["items"][0]["url"] != before["items"][0]["url"]  # #c= changes: re-prepared
    with app.app_context():
        for mid in (ids[0], ids[2]):
            db.session.delete(db.session.get(Media, mid))
        db.session.commit()
    assert _manifest(client, token)["items"] == []  # no image left: not playable


def test_newest_order_and_new_uploads(app, client):
    ids = _images(app, 4)
    _images(app, 1, start=10, kind="web")
    media_id = add_item(app, KEY, _config(source="newest", newest_count=3))
    token = paired_with(app, media_id)
    first = _manifest(client, token)
    assert [m["media_id"] for m in first["items"][0]["plugin"]["media"]] == ids[:0:-1]
    newer, = _images(app, 1, start=20)
    second = _manifest(client, token)
    assert [m["media_id"] for m in second["items"][0]["plugin"]["media"]] == [newer, ids[3], ids[2]]
    assert second["version"] != first["version"]


def test_newest_with_an_empty_library_is_not_played(app, client):
    media_id = add_item(app, KEY, _config(source="newest"))
    token = paired_with(app, media_id)
    assert _manifest(client, token)["items"] == []
    _images(app, 1)
    assert len(_manifest(client, token)["items"]) == 1


def test_playable_ignores_non_image_ids(app):
    ids = _images(app, 1)
    media_id = add_item(app, KEY, _config(media_ids=ids))
    with app.app_context():
        media = db.session.get(Media, media_id)
        media.plugin_config = {**media.plugin_config, "media_ids": [9999]}
        db.session.commit()
        assert service.is_playable_plugin(db.session.get(Media, media_id)) is False


# --- admin preview ------------------------------------------------------------------------

def test_preview_uses_admin_media_urls(app, client):
    ids = _images(app, 2)
    media_id = add_item(app, KEY, _config(media_ids=ids))
    login(client, "admin")
    page = client.get(f"/plugins/items/{media_id}/preview")
    assert page.status_code == 200
    html = page.data.decode()
    assert "player/plugins/image_library/native.js" in html
    assert "player/plugins/image_library/native.css" in html
    assert f"/media/{ids[0]}/file" in html and f"{API}/media/" not in html
    form = client.post(f"/plugins/{KEY}/preview", data={
        "name": "X", "source": "selection", "media_ids": [str(ids[1])], "newest_count": "10",
        "mode": "collage", "grid": "3x3", "seconds_per_image": "5", "transition": "cut",
        "fit": "cover", "background": "black", "caption": "none"})
    assert form.status_code == 200
    media = form.get_json()["item"]["plugin"]["media"]
    assert [m["url"] for m in media] == [f"/media/{ids[1]}/file"]


# --- native.js ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def js():
    if shutil.which("node") is None:
        pytest.skip("needs Node.js")
    out = subprocess.run(["node", str(ROOT / "js" / "plugin_image_library.js"),
                          str(STATIC / "native.js")],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_settings(js):
    d = js["settingsDefault"]
    assert d["mode"] == "slideshow" and d["periodMs"] == 8000 and d["blur"] is True
    odd = js["settingsOdd"]
    assert odd["mode"] == "slideshow" and odd["periodMs"] == 120000
    assert odd["transition"] == "fade" and odd["blur"] is False and odd["shuffle"] is False
    assert js["settingsCollage"]["tiles"] == 9 and js["settingsCollage"]["blur"] is False
    assert [e["url"] for e in js["entries"]] == ["/a", "/b"]


def test_js_order_and_tiles(js):
    assert js["orderPlain"] == list(range(1, 9))
    assert sorted(js["orderShuffled"]) == list(range(1, 9))
    assert js["orderShuffled"] != list(range(1, 9))
    assert js["pickTile"] == {"neverLast": True, "all": True, "single": 0}


def test_js_slideshow(js):
    s = js["slideshow"]
    assert s["settled"] == "ok" and s["first"] == ["/m/1.jpg"]
    assert "pl-imglib-t-fade" in s["rootClass"]
    assert s["preloaded"] == ["/m/1.jpg", "/m/2.jpg", "/m/3.jpg"]  # two ahead
    assert s["before"] == ["/m/1.jpg"] and s["during"] == ["/m/1.jpg", "/m/2.jpg"]
    assert s["classes"] == ["pl-imglib-frame pl-imglib-out", "pl-imglib-frame pl-imglib-on"]
    assert s["after"] == ["/m/2.jpg"] and s["wrapped"] == ["/m/1.jpg"]
    assert s["teardownSrcs"] == 0


def test_js_failures(js):
    assert js["skip"] == ["/m/3.jpg"]
    assert js["allFail"] == {"settled": "ok", "card": True, "degraded": 1, "frames": 0}
    assert js["empty"] == {"cards": [["Image library", "No images to show"]], "degraded": 1}


def test_js_pause_and_single_image(js):
    p = js["pause"]
    assert "pl-imglib-paused" in p["pausedClass"] and "pl-imglib-paused" not in p["resumedClass"]
    assert p["held"] == ["/m/1.jpg"] and p["resumed"] == ["/m/2.jpg"]
    assert js["single"] == {"srcs": ["/m/1.jpg"], "requests": 1}


def test_js_kenburns_blur_caption(js):
    k = js["kenburns"]
    assert "pl-imglib-kb" in k["cls"] and k["duration"] == "4500ms"
    assert k["framesDuring"] == 2 and "pl-imglib-kb" in k["newCls"]
    b = js["blur"]
    assert b["kids"] == ["pl-imglib-bg", "pl-imglib-img pl-imglib-fit-contain", "pl-imglib-cap"]
    assert b["bgSrc"] == "/m/1.jpg" and b["caption"] == "Photo 1"
    assert b["cover"] == ["pl-imglib-img pl-imglib-fit-cover"]


def test_js_collage(js):
    c = js["collage"]
    assert c["gridClass"] == "pl-imglib-grid pl-imglib-grid-2" and c["tiles"] == 4
    assert c["first"] == ["/m/1.jpg", "/m/2.jpg", "/m/3.jpg", "/m/4.jpg"]
    changed = [i for i in range(4) if c["after1"][i] != c["first"][i]]
    assert len(changed) == 1 and c["after1"][changed[0]] == "/m/5.jpg"
    assert sum(a != b for a, b in zip(c["after1"], c["after2"])) == 1 and "/m/6.jpg" in c["after2"]
    assert c["dupes"] == 0
    small = c["small"]  # fewer images than tiles: repeats, and no changes
    assert small["tiles"] == 9 and small["before"] == small["after"]
    assert small["before"][:4] == ["/m/1.jpg", "/m/2.jpg", "/m/3.jpg", "/m/4.jpg"]
