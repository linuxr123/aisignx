"""Mute and volume per playlist item: videos and YouTube."""
import pytest

from aisignx.extensions import db
from aisignx.models import Playlist, PlaylistItem

from .conftest import login, make_user
from .plugin_fixtures import add_item, make_plugin_app

VID = "dQw4w9WgXcQ"


@pytest.fixture
def app(tmp_path, monkeypatch):
    app = make_plugin_app(tmp_path, monkeypatch)
    yield app
    with app.app_context():
        db.engine.dispose()


def _playlist(app, media_id):
    with app.app_context():
        pl = Playlist(name="P")
        pl.items.append(PlaylistItem(media_id=media_id, position=0))
        db.session.add(pl)
        db.session.commit()
        return pl.id, pl.items[0].id


def _admin(app):
    make_user(app, "admin", "admin")
    c = app.test_client()
    login(c, "admin")
    return c


def test_youtube_item_has_mute_and_volume_controls(app):
    c = _admin(app)
    pid, _ = _playlist(app, add_item(app, "youtube", {"source": "videos", "videos": VID}))
    html = c.get(f"/playlists/{pid}").get_data(as_text=True)
    assert 'name="mute"' in html and 'name="volume"' in html


def test_other_plugins_have_no_sound_controls(app):
    c = _admin(app)
    pid, _ = _playlist(app, add_item(app, "clock", {}))
    html = c.get(f"/playlists/{pid}").get_data(as_text=True)
    assert 'name="mute"' not in html and 'name="volume"' not in html


def test_saving_item_sets_mute_and_volume(app):
    c = _admin(app)
    pid, iid = _playlist(app, add_item(app, "youtube", {"source": "videos", "videos": VID}))
    form = {"duration": "", "transition": "", "fit_mode": "fit", "volume": "35"}  # Mute unchecked
    assert c.post(f"/playlists/{pid}/items/{iid}", data=form).status_code in (302, 303)
    with app.app_context():
        item = db.session.get(PlaylistItem, iid)
        assert item.mute is False and item.volume == 35
    form["volume"] = "150"
    c.post(f"/playlists/{pid}/items/{iid}", data=form)
    with app.app_context():
        assert db.session.get(PlaylistItem, iid).volume == 35  # refused, unchanged


def test_duplicate_keeps_volume(app):
    from aisignx.playlists import service
    c = _admin(app)
    pid, iid = _playlist(app, add_item(app, "youtube", {"source": "videos", "videos": VID}))
    with app.app_context():
        db.session.get(PlaylistItem, iid).volume = 20
        db.session.commit()
        copy = service.duplicate(db.session.get(Playlist, pid))
        assert copy.items[0].volume == 20
