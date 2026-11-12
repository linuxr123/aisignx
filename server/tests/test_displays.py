from datetime import timedelta

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from aisignx.extensions import db
from aisignx.models import Display, PairingCode, Playlist
from aisignx.player.broker import get_broker
from aisignx.timeutil import utcnow

from .conftest import login, make_user


@pytest.fixture
def playlist_id(app):
    with app.app_context():
        playlist = Playlist(name="Morning")
        db.session.add(playlist)
        db.session.commit()
        return playlist.id


def _as(client, app, role):
    make_user(app, role, role)
    login(client, role)
    return client


# --- Role matrix ------------------------------------------------------------------------

def test_viewer_can_read_but_not_change(configured, client, paired_display):
    display_id, _ = paired_display
    _as(client, configured, "viewer")
    assert client.get("/displays/").status_code == 200
    assert client.get(f"/displays/{display_id}").status_code == 200
    status = client.get("/displays/status.json")
    assert status.status_code == 200
    assert status.json["displays"][0]["id"] == display_id
    assert status.json["server_time"].endswith("Z")

    page = client.get("/displays/").get_data(as_text=True)
    assert "Pair a display" not in page and "Unpair" not in page

    assert client.get("/displays/pair").status_code == 403
    assert client.get(f"/displays/{display_id}/edit").status_code == 403
    for action in ("edit", "reload", "unpair", "delete"):
        assert client.post(f"/displays/{display_id}/{action}").status_code == 403
    with configured.app_context():
        assert db.session.get(Display, display_id).is_paired


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_editor_and_admin_can_manage(configured, client, paired_display, playlist_id, role):
    display_id, _ = paired_display
    if role == "admin":
        login(client, "admin")
    else:
        _as(client, configured, role)
    assert "Pair a display" in client.get("/displays/").get_data(as_text=True)
    assert client.get("/displays/pair").status_code == 200
    assert client.get(f"/displays/{display_id}/edit").status_code == 200

    resp = client.post(f"/displays/{display_id}/edit",
                       data={"name": "Front desk", "playlist": playlist_id})
    assert resp.status_code == 302
    with configured.app_context():
        display = db.session.get(Display, display_id)
        assert (display.name, display.playlist_id) == ("Front desk", playlist_id)

    assert client.post(f"/displays/{display_id}/reload").status_code == 302

    resp = client.post(f"/displays/{display_id}/unpair", follow_redirects=True)
    assert b"Unpaired Front desk" in resp.data
    with configured.app_context():
        display = db.session.get(Display, display_id)
        assert display.token_hash is None and display.paired_at is None

    resp = client.post(f"/displays/{display_id}/delete", follow_redirects=True)
    assert b"Deleted display Front desk." in resp.data
    with configured.app_context():
        assert db.session.get(Display, display_id) is None


def test_logged_out_redirects(configured, client, paired_display):
    resp = client.get("/displays/status.json")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]
    assert client.get("/displays/").status_code == 302


def test_edit_clears_playlist_and_requires_name(app, admin_client, paired_display, playlist_id):
    display_id, _ = paired_display
    admin_client.post(f"/displays/{display_id}/edit", data={"name": "Lobby", "playlist": playlist_id})
    resp = admin_client.post(f"/displays/{display_id}/edit", data={"name": "", "playlist": 0})
    assert resp.status_code == 200  # re-rendered with an error
    admin_client.post(f"/displays/{display_id}/edit", data={"name": "Lobby", "playlist": 0})
    with app.app_context():
        assert db.session.get(Display, display_id).playlist_id is None


def test_pair_new_requires_name(app, admin_client):
    resp = admin_client.post("/displays/pair", data={"code": "K7PQ4X", "pair_as": 0, "name": "",
                                                     "playlist": 0})
    assert resp.status_code == 200
    assert b"Give the new display a name." in resp.data
    with app.app_context():
        assert Display.query.count() == 0


def test_pair_bad_code_flashes_generic_error(app, admin_client):
    resp = admin_client.post("/displays/pair", data={"code": "K7P-Q4X", "pair_as": 0,
                                                     "name": "Hall", "playlist": 0})
    assert resp.status_code == 200
    assert b"That code is not valid or has expired." in resp.data
    with app.app_context():
        assert Display.query.count() == 0


def test_detail_shows_details_without_token(app, admin_client, paired_display):
    display_id, token = paired_display
    with app.app_context():
        display = db.session.get(Display, display_id)
        display.client_id = "abcdef0123456789"
        display.screen_width, display.screen_height = 1920, 1080
        display.player_version = "web-3.0.0"
        db.session.commit()
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert "abcdef01" in page and "abcdef0123456789" not in page
    assert "1920" in page and "web-3.0.0" in page and "Not connected" in page
    assert token not in page
    assert admin_client.get("/displays/999").status_code == 404


def test_empty_index(admin_client):
    page = admin_client.get("/displays/").get_data(as_text=True)
    assert "No displays yet." in page and "/player/" in page


# --- State ------------------------------------------------------------------------------

def test_state_with_sqlite_naive_datetimes(app, paired_display):
    display_id, _ = paired_display
    limit = timedelta(seconds=90)
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display.state(utcnow(), limit) == "never"

        for ago, expected in ((30, "online"), (200, "offline")):
            display.last_seen_at = utcnow() - timedelta(seconds=ago)
            db.session.commit()
            db.session.expire_all()  # reload from SQLite: comes back naive
            display = db.session.get(Display, display_id)
            assert display.state(utcnow(), limit) == expected

        display.token_hash = None
        db.session.commit()
        assert display.state(utcnow(), limit) == "unpaired"


def test_status_json_states(app, admin_client, paired_display):
    display_id, _ = paired_display
    with app.app_context():
        db.session.get(Display, display_id).last_seen_at = utcnow()
        db.session.commit()
    row = admin_client.get("/displays/status.json").json["displays"][0]
    assert row["state"] == "online" and row["last_seen"].endswith("Z") and row["live"] is False


# --- Reload -----------------------------------------------------------------------------

def test_reload_flashes_by_connection(app, admin_client, paired_display):
    display_id, _ = paired_display
    resp = admin_client.post(f"/displays/{display_id}/reload", follow_redirects=True)
    assert b"The display is not connected right now." in resp.data

    with app.app_context():
        sub = get_broker().subscribe(display_id)
    resp = admin_client.post(f"/displays/{display_id}/reload", follow_redirects=True)
    assert b"Reload sent." in resp.data
    assert sub.queue.get_nowait() == ("command", {"action": "reload"})

    page = admin_client.get("/displays/").get_data(as_text=True)
    assert 'data-display-live="%d" title' % display_id in page  # live dot shown
    assert ">Connected<" in admin_client.get(f"/displays/{display_id}").get_data(as_text=True)


def _events(sub):
    out = []
    while not sub.queue.empty():
        out.append(sub.queue.get_nowait())
    return out


def test_unpair_and_delete_revoke_streams(app, admin_client, paired_display):
    display_id, _ = paired_display
    with app.app_context():
        broker = get_broker()
        sub = broker.subscribe(display_id)
    admin_client.post(f"/displays/{display_id}/unpair")
    assert ("revoked", {}) in _events(sub)
    with app.app_context():
        sub = broker.subscribe(display_id)
    admin_client.post(f"/displays/{display_id}/delete")
    assert ("revoked", {}) in _events(sub)
    assert broker.count() == 0


# --- Cascade ----------------------------------------------------------------------------

def test_delete_cascades_pairing_codes(app, admin_client, paired_display):
    display_id, _ = paired_display
    with app.app_context():
        now = utcnow()
        db.session.add(PairingCode(code="K7PQ4X", secret_hash="a" * 64, created_at=now,
                                   expires_at=now + timedelta(minutes=2), display_id=display_id))
        db.session.commit()
    admin_client.post(f"/displays/{display_id}/delete")
    with app.app_context():
        assert PairingCode.query.count() == 0


# --- Dashboard and nav ------------------------------------------------------------------

def test_dashboard_counts_and_nav(app, admin_client, paired_display):
    display_id, _ = paired_display
    with app.app_context():
        db.session.add(Display(name="Unpaired one"))
        db.session.get(Display, display_id).last_seen_at = utcnow()
        db.session.commit()
    page = admin_client.get("/dashboard").get_data(as_text=True)
    assert "Displays" in page and "Online" in page
    assert 'href="/displays/"' in page
    assert "Scheduling arrives in the next phase." in page
    with app.app_context():
        from aisignx.displays.service import dashboard_counts
        assert dashboard_counts() == (2, 1)


# --- Migration and CLI ------------------------------------------------------------------

def test_migration_downgrade_and_upgrade(app):
    with app.app_context():
        downgrade(revision="94c490f7841b")
        tables = inspect(db.engine).get_table_names()
        assert "displays" not in tables and "pairing_codes" not in tables
        upgrade()
        tables = inspect(db.engine).get_table_names()
        assert "displays" in tables and "pairing_codes" in tables


def test_serve_help(app):
    result = app.test_cli_runner().invoke(args=["serve", "--help"])
    assert result.exit_code == 0, result.output
    assert "--threads" in result.output
