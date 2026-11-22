"""The playlist "Add media" picker: filters, paging, selection across filters, and the page markup."""
import re

import pytest

from aisignx.extensions import db
from aisignx.models import ImageSource, Media, Playlist, PlaylistItem
from aisignx.playlists import picker

from .conftest import login, make_user


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _library(app):
    """3 uploads (2 images, 1 video), a clock plugin, a web page, and 60 images imported from a source. Returns (playlist id, source id)."""
    with app.app_context():
        src = ImageSource(name="Family", kind="folder", location="x")
        pl = Playlist(name="Lobby")
        db.session.add_all([src, pl])
        db.session.flush()
        rows = [Media(name="Logo", media_type="image", stored_name="a1.png"), Media(name="Menu photo", media_type="image", stored_name="a2.png"),
                Media(name="Intro", media_type="video", stored_name="v1.mp4"), Media(name="Clock", media_type="plugin", plugin_key="clock"),
                Media(name="Weather", media_type="plugin", plugin_key="weather"), Media(name="News site", media_type="web", url="https://example.com")]
        rows += [Media(name=f"holiday {i:02d}", media_type="image", stored_name=f"s{i}.png", source_id=src.id, source_key=f"k{i}") for i in range(60)]
        db.session.add_all(rows)
        db.session.flush()
        db.session.add(PlaylistItem(playlist_id=pl.id, media_id=rows[0].id, position=0))
        db.session.commit()
        return pl.id, src.id


def _get(admin, pid, **params):
    r = admin.get(f"/playlists/{pid}/media-picker", query_string=params)
    assert r.status_code == 200
    return r.get_json()


def test_by_default_only_uploads_and_plugins_show_not_the_imported_images(app, admin):
    pid, sid = _library(app)
    d = _get(admin, pid)
    assert d["total"] == 6 and {i["name"] for i in d["items"]} == {"Logo", "Menu photo", "Intro", "Clock", "Weather", "News site"}
    assert d["uploads"] == 6 and d["sources"] == [{"id": sid, "name": "Family", "count": 60}]
    assert d["types"] == {"image": 2, "video": 1, "web": 1, "plugin": 2}


def test_filters_by_kind_plugin_source_and_search(app, admin):
    pid, sid = _library(app)
    assert {i["name"] for i in _get(admin, pid, type="video")["items"]} == {"Intro"}
    assert {i["name"] for i in _get(admin, pid, type="plugin")["items"]} == {"Clock", "Weather"}
    only = _get(admin, pid, type="plugin", plugin="clock")
    assert [i["name"] for i in only["items"]] == ["Clock"] and [p["key"] for p in only["plugins"]] == ["clock", "weather"]   # chips keep every plugin
    assert _get(admin, pid, source=str(sid))["total"] == 60 and _get(admin, pid, source="all")["total"] == 66
    assert {i["name"] for i in _get(admin, pid, q="menu")["items"]} == {"Menu photo"}
    assert _get(admin, pid, source="all", q="holiday 0")["total"] == 10
    assert _get(admin, pid, q="%")["total"] == 6 and _get(admin, pid, q="_")["total"] == 6            # wildcards are not special
    assert _get(admin, pid, type="nonsense")["total"] == 6                                                # an unknown kind is ignored


def test_paging_sorting_and_the_ids_of_everything_that_matches(app, admin):
    pid, sid = _library(app)
    first = _get(admin, pid, source=str(sid), sort="name")
    assert len(first["items"]) == picker.PER_PAGE and first["pages"] == 2 and first["page"] == 1
    assert first["items"][0]["name"] == "holiday 00" and len(first["ids"]) == 60 and first["truncated"] is False
    second = _get(admin, pid, source=str(sid), sort="name", page=2)
    assert len(second["items"]) == 60 - picker.PER_PAGE and second["items"][0]["name"] == "holiday 48"
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    assert _get(admin, pid, source=str(sid), page=99)["items"] == []
    assert _get(admin, pid, page="x")["page"] == 1


def test_items_already_in_the_playlist_are_marked_and_can_be_hidden(app, admin):
    pid, _sid = _library(app)
    d = _get(admin, pid)
    assert [i["added"] for i in d["items"] if i["name"] == "Logo"] == [True]
    hidden = _get(admin, pid, hide_added="1")
    assert "Logo" not in {i["name"] for i in hidden["items"]} and hidden["total"] == 5


def test_the_picker_needs_a_login_and_a_real_playlist(app, admin, client):
    pid, _sid = _library(app)
    assert app.test_client().get(f"/playlists/{pid}/media-picker").status_code in (302, 401)
    assert admin.get("/playlists/9999/media-picker").status_code == 404


def test_selected_media_are_added_through_the_normal_form(app, admin):
    pid, sid = _library(app)
    page = admin.get(f"/playlists/{pid}").get_data(as_text=True)
    assert "data-media-picker" in page and "Select all matching" in page and "media_picker.js" in page
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)
    ids = _get(admin, pid, source=str(sid), sort="name")["ids"][:5]
    r = admin.post(f"/playlists/{pid}/items", data={"csrf_token": tok, "media_ids": ids}, follow_redirects=True)
    assert b"Added 5 items" in r.data
    with app.app_context():
        assert PlaylistItem.query.filter_by(playlist_id=pid).count() == 6
    js = admin.get("/static/js/media_picker.js").get_data(as_text=True)
    assert "selected = new Map()" in js and "hide_added" in js           # the selection lives outside the filtered grid
