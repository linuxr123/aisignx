"""Plugin items in the library: create, edit, roles, audit, thumbnails, playlists, API."""
import json

from aisignx.extensions import db
from aisignx.models import AuditLog, Media, Playlist, PlaylistItem, Setting

from .conftest import login, make_token, make_user
from .plugin_fixtures import add_item, padmin, papp, pclient  # noqa: F401

FORM = {"name": "Lobby echo", "duration": "", "message": " Hi there ", "note": "secret-note",
        "tz": "", "place.q": "52.37, 4.89", "place.lat": "", "place.lon": "", "place.label": "",
        "count": "5", "color": "blue", "link": "", "body": "", "refresh": ""}


def _audit(app, action):
    with app.app_context():
        return [(r.target_id, json.loads(r.detail) if r.detail else None)
                for r in AuditLog.query.filter_by(action=action).order_by(AuditLog.id)]


def test_editor_creates_an_item(papp, pclient):
    make_user(papp, "admin", "admin")
    make_user(papp, "ed", "editor")
    login(pclient, "ed")
    page = pclient.get("/plugins/new")
    assert page.status_code == 200 and b"Echo" in page.data
    form = pclient.get("/plugins/new/echo")
    assert form.status_code == 200 and b'name="place.q"' in form.data
    assert "Content-Security-Policy" in form.headers
    resp = pclient.post("/plugins/new/echo", data=FORM)
    assert resp.status_code == 303
    with papp.app_context():
        media = Media.query.filter_by(media_type="plugin").one()
        assert media.plugin_key == "echo" and media.name == "Lobby echo"
        assert media.default_duration == 12  # plugin.json default_duration
        cfg = media.plugin_config
        assert cfg["message"] == "Hi there" and cfg["count"] == 5 and cfg["note"] == "secret-note"
        assert cfg["place"] == {"q": "52.37, 4.89", "lat": 52.37, "lon": 4.89,
                                "label": "52.37, 4.89"}
        assert media.thumbnail_name and media.thumbnail_status == "ok"
        media_id = media.id
    rows = _audit(papp, "media.create_plugin")
    assert rows == [(media_id, {"plugin": "echo"})]


def test_invalid_form_shows_errors(padmin, papp):
    resp = padmin.post("/plugins/new/echo", data={**FORM, "message": "", "count": "99",
                                                  "name": ""})
    assert resp.status_code == 400
    assert b"This field is required." in resp.data and b"Must be between 1 and 10." in resp.data
    resp = padmin.post("/plugins/new/echo", data={**FORM, "message": "forbidden"})
    assert resp.status_code == 400 and b"This message is not allowed." in resp.data
    with papp.app_context():
        assert Media.query.count() == 0


def test_viewer_cannot_create_or_edit(papp, pclient):
    make_user(papp, "admin", "admin")
    make_user(papp, "viewer", "viewer")
    media_id = add_item(papp)
    login(pclient, "viewer")
    assert pclient.get("/plugins/new").status_code == 403
    assert pclient.get("/plugins/new/echo").status_code == 403
    assert pclient.post("/plugins/new/echo", data=FORM).status_code == 403
    assert pclient.get(f"/plugins/items/{media_id}/edit").status_code == 403
    assert pclient.post(f"/plugins/items/{media_id}/edit", data=FORM).status_code == 403
    assert pclient.get("/plugins/").status_code == 200
    assert pclient.get(f"/plugins/items/{media_id}/preview").status_code == 200


def test_disabled_plugin_hidden_from_new(padmin, papp):
    with papp.app_context():
        Setting.set("plugins.echo.enabled", "0")
        db.session.commit()
    page = padmin.get("/plugins/new")
    assert b"Add Echo<" not in page.data and b"Add Echo native" in page.data
    assert padmin.get("/plugins/new/echo").status_code == 404
    assert padmin.get("/plugins/new/nope").status_code == 404


def test_edit_records_changed_keys_only(padmin, papp):
    media_id = add_item(papp)
    page = padmin.get(f"/plugins/items/{media_id}/edit")
    assert page.status_code == 200 and b'value="Hello"' in page.data
    with papp.app_context():
        old_thumb = db.session.get(Media, media_id).thumbnail_name
    data = {**FORM, "name": "Renamed", "message": "Hello", "note": "new-secret",
            "place.q": "", "count": "3", "color": "red", "refresh": "60", "duration": "20"}
    assert padmin.post(f"/plugins/items/{media_id}/edit", data=data).status_code == 303
    with papp.app_context():
        media = db.session.get(Media, media_id)
        assert media.name == "Renamed" and media.default_duration == 20
        assert media.plugin_config["note"] == "new-secret"
        assert media.thumbnail_name != old_thumb  # the title card shows the name
    (target, detail), = _audit(papp, "media.update")
    assert target == media_id
    assert detail == {"changed": ["name", "default_duration", "config.note"]}
    assert "new-secret" not in json.dumps(detail)


def test_auto_duration_hook(padmin, papp):
    data = {"name": "N", "duration": "", "words": "hi", "mute": "1", "seconds": "7"}
    assert padmin.post("/plugins/new/echo_native", data=data).status_code == 303
    with papp.app_context():
        assert Media.query.filter_by(plugin_key="echo_native").one().default_duration == 14


def test_library_filter_badge_and_links(padmin, papp):
    media_id = add_item(papp)
    page = padmin.get("/media/?type=plugin")
    assert page.status_code == 200
    html = page.data.decode()
    assert "Echo item" in html and "text-bg-success" in html
    assert f"/plugins/items/{media_id}/preview" in html and "/plugins/new" in html
    assert 'data-initial-type="plugin"' in html and 'data-type-filter="plugin"' in html
    assert "Echo item" not in padmin.get("/media/?type=image").data.decode()
    resp = padmin.get(f"/media/{media_id}/edit")
    assert resp.status_code == 302 and resp.location.endswith(f"/plugins/items/{media_id}/edit")


def test_media_replace_refuses_plugin_items(padmin, papp):
    from io import BytesIO

    media_id = add_item(papp)
    resp = padmin.post(f"/media/{media_id}/replace",
                       data={"file": (BytesIO(b"x"), "a.png")},
                       content_type="multipart/form-data")
    assert resp.status_code == 302
    with papp.app_context():
        assert db.session.get(Media, media_id).stored_name is None


def test_playlist_add_and_delete_cascade(padmin, papp):
    media_id = add_item(papp)
    with papp.app_context():
        playlist = Playlist(name="P")
        db.session.add(playlist)
        db.session.commit()
        pid = playlist.id
    padmin.post(f"/playlists/{pid}/items", data={"media_ids": [media_id]})
    with papp.app_context():
        item = PlaylistItem.query.filter_by(playlist_id=pid).one()
        assert item.media_id == media_id and item.effective_duration == 12
    assert b"Echo item" in padmin.get(f"/playlists/{pid}").data
    assert padmin.post(f"/media/{media_id}/delete").status_code == 302
    with papp.app_context():
        assert db.session.get(Media, media_id) is None
        assert PlaylistItem.query.count() == 0


def test_thumbnail_regenerate_draws_a_title_card(padmin, papp):
    media_id = add_item(papp)
    with papp.app_context():
        before = db.session.get(Media, media_id).thumbnail_name
    assert padmin.post(f"/media/{media_id}/thumbnail/refresh").status_code == 302
    with papp.app_context():
        media = db.session.get(Media, media_id)
        assert media.thumbnail_status == "ok" and media.thumbnail_name != before


def test_token_api_media_json_and_replace(papp, pclient):
    admin_id = make_user(papp, "admin", "admin")
    media_id = add_item(papp)
    token = make_token(papp, admin_id)
    headers = {"Authorization": f"Bearer {token}"}
    body = pclient.get(f"/api/v1/media/{media_id}", headers=headers).get_json()
    assert body["type"] == "plugin"
    assert body["plugin"]["key"] == "echo" and body["plugin"]["config"]["message"] == "Hello"
    assert "note" not in body["plugin"]["config"]
    assert "keep-me-private" not in json.dumps(body)
    from io import BytesIO

    from .conftest import png_bytes

    resp = pclient.post(f"/api/v1/media/{media_id}/replace", headers=headers,
                        data={"file": (BytesIO(png_bytes()), "a.png")},
                        content_type="multipart/form-data")
    assert resp.status_code == 409 and resp.get_json()["error"] == "not_replaceable"
    listing = pclient.get("/api/v1/media", headers=headers).get_json()
    assert "keep-me-private" not in json.dumps(listing)
