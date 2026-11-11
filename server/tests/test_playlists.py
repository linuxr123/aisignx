import pytest

from aisignx.extensions import db
from aisignx.models import Media, Playlist, PlaylistItem

from .conftest import login, make_user


@pytest.fixture
def library(app):
    """Three media items: image (10s), web page (20s), video (12.3s detected)."""
    with app.app_context():
        items = [
            Media(name="Logo", media_type="image", default_duration=10),
            Media(name="Menu", media_type="web", url="https://example.com", default_duration=20),
            Media(name="Promo", media_type="video", duration_seconds=12.3, default_duration=10),
        ]
        db.session.add_all(items)
        db.session.commit()
        return [m.id for m in items]


def _create(client, name="Lobby", transition="fade"):
    resp = client.post("/playlists/new", data={"name": name, "default_transition": transition})
    assert resp.status_code == 302
    return int(resp.headers["Location"].rstrip("/").rsplit("/", 1)[-1])


def _items(app, playlist_id):
    with app.app_context():
        playlist = db.session.get(Playlist, playlist_id)
        return [(i.id, i.media.name) for i in playlist.items]


def test_create_add_and_total_duration(app, admin_client, library):
    pid = _create(admin_client)
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": library})
    assert [name for _, name in _items(app, pid)] == ["Logo", "Menu", "Promo"]
    with app.app_context():
        playlist = db.session.get(Playlist, pid)
        assert playlist.total_duration == 10 + 20 + 13  # video rounds up
        assert all(i.effective_transition == "fade" for i in playlist.items)
    page = admin_client.get(f"/playlists/{pid}")
    assert page.status_code == 200 and b"Promo" in page.data
    assert admin_client.get("/playlists/").status_code == 200


def test_same_media_can_appear_twice(app, admin_client, library):
    pid = _create(admin_client)
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": [library[0]]})
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": [library[0]]})
    assert [name for _, name in _items(app, pid)] == ["Logo", "Logo"]


def test_unknown_media_ids_ignored(app, admin_client, library):
    pid = _create(admin_client)
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": [9999]})
    assert _items(app, pid) == []


def test_reorder_json(app, admin_client, library):
    pid = _create(admin_client)
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": library})
    ids = [i for i, _ in _items(app, pid)]

    resp = admin_client.post(f"/playlists/{pid}/reorder", json={"item_ids": ids[::-1]})
    assert resp.status_code == 200 and resp.json["ok"]
    assert [name for _, name in _items(app, pid)] == ["Promo", "Menu", "Logo"]

    # Incomplete, duplicated or foreign ids are rejected and change nothing.
    for bad in (ids[:2], [ids[0], ids[0], ids[1]], ids[:2] + [424242], "nope"):
        assert admin_client.post(f"/playlists/{pid}/reorder", json={"item_ids": bad}).status_code == 400
    assert [name for _, name in _items(app, pid)] == ["Promo", "Menu", "Logo"]


def test_move_up_down(app, admin_client, library):
    pid = _create(admin_client)
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": library})
    ids = [i for i, _ in _items(app, pid)]
    admin_client.post(f"/playlists/{pid}/items/{ids[2]}/move", data={"direction": "up"})
    assert [name for _, name in _items(app, pid)] == ["Logo", "Promo", "Menu"]
    admin_client.post(f"/playlists/{pid}/items/{ids[0]}/move", data={"direction": "up"})  # already first
    assert [name for _, name in _items(app, pid)] == ["Logo", "Promo", "Menu"]


def test_update_and_remove_item(app, admin_client, library):
    pid = _create(admin_client)
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": library})
    ids = [i for i, _ in _items(app, pid)]

    admin_client.post(f"/playlists/{pid}/items/{ids[0]}",
                      data={"duration": "5", "transition": "slide-left", "fit_mode": "fill"})
    admin_client.post(f"/playlists/{pid}/items/{ids[2]}",
                      data={"duration": "", "transition": "", "fit_mode": "fit"})  # mute unchecked
    admin_client.post(f"/playlists/{pid}/items/{ids[1]}",
                      data={"transition": "explode", "fit_mode": "fit"})  # invalid -> ignored
    with app.app_context():
        first, second, third = (db.session.get(PlaylistItem, i) for i in ids)
        assert (first.effective_duration, first.effective_transition, first.fit_mode) == (5, "slide-left", "fill")
        assert second.transition is None
        assert third.mute is False and third.effective_duration == 13

    admin_client.post(f"/playlists/{pid}/items/{ids[1]}/delete")
    assert [name for _, name in _items(app, pid)] == ["Logo", "Promo"]
    with app.app_context():
        assert [i.position for i in db.session.get(Playlist, pid).items] == [0, 1]
        assert Media.query.count() == 3  # removing an item never deletes media


def test_item_routes_check_playlist_ownership(app, admin_client, library):
    a, b = _create(admin_client, "A"), _create(admin_client, "B")
    admin_client.post(f"/playlists/{a}/items", data={"media_ids": [library[0]]})
    item_id = _items(app, a)[0][0]
    assert admin_client.post(f"/playlists/{b}/items/{item_id}/delete").status_code == 404


def test_edit_duplicate_delete(app, admin_client, library):
    pid = _create(admin_client)
    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": library})
    admin_client.post(f"/playlists/{pid}/edit", data={"name": "Lobby 2", "default_transition": "random"})
    with app.app_context():
        assert db.session.get(Playlist, pid).default_transition == "random"

    resp = admin_client.post(f"/playlists/{pid}/duplicate")
    copy_id = int(resp.headers["Location"].rsplit("/", 1)[-1])
    assert [n for _, n in _items(app, copy_id)] == ["Logo", "Menu", "Promo"]

    admin_client.post(f"/playlists/{pid}/delete")
    with app.app_context():
        assert db.session.get(Playlist, pid) is None
        assert PlaylistItem.query.filter_by(playlist_id=pid).count() == 0
        assert Media.query.count() == 3


def test_viewer_is_read_only(app, client, configured, library):
    with app.app_context():
        db.session.add(Playlist(name="Seen"))
        db.session.commit()
        pid = Playlist.query.one().id
    make_user(app, "val", "viewer")
    login(client, "val")
    assert client.get("/playlists/").status_code == 200
    page = client.get(f"/playlists/{pid}")
    assert page.status_code == 200 and b"Add media" not in page.data
    assert client.get("/playlists/new").status_code == 403
    assert client.post(f"/playlists/{pid}/items", data={"media_ids": library}).status_code == 403
    assert client.post(f"/playlists/{pid}/reorder", json={"item_ids": []}).status_code == 403
    assert client.post(f"/playlists/{pid}/delete").status_code == 403


def test_editor_can_manage(app, client, configured, library):
    make_user(app, "ed", "editor")
    login(client, "ed")
    pid = _create(client)
    assert client.post(f"/playlists/{pid}/items", data={"media_ids": library}).status_code == 302
