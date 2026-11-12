"""Display groups: role matrix, CRUD, membership moves, delete semantics."""
import queue

import pytest

from aisignx.extensions import db
from aisignx.models import Display, DisplayGroup, EmergencyMessage, Playlist, Schedule
from aisignx.player.broker import get_broker

from .conftest import login, make_user


def _as(client, app, role):
    make_user(app, role, role)
    login(client, role)
    return client


def _drain(sub) -> list:
    events = []
    while True:
        try:
            events.append(sub.queue.get_nowait())
        except queue.Empty:
            return events


@pytest.fixture
def listener(app):
    with app.app_context():
        broker = get_broker()
        sub = broker.subscribe(999_999)
    yield sub
    broker.unsubscribe(sub)


def _display(app, name, group_id=None) -> int:
    with app.app_context():
        d = Display(name=name, group_id=group_id)
        db.session.add(d)
        db.session.commit()
        return d.id


def _group(app, name) -> int:
    with app.app_context():
        g = DisplayGroup(name=name)
        db.session.add(g)
        db.session.commit()
        return g.id


# --- Role matrix ------------------------------------------------------------------------

def test_viewer_can_read_but_not_change(configured, client):
    group_id = _group(configured, "Lobby")
    _as(client, configured, "viewer")
    page = client.get("/groups/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Lobby" in html and "New group" not in html and "Delete" not in html
    assert client.get("/groups/new").status_code == 403
    assert client.get(f"/groups/{group_id}/edit").status_code == 403
    assert client.post("/groups/new", data={"name": "X"}).status_code == 403
    assert client.post(f"/groups/{group_id}/edit", data={"name": "X"}).status_code == 403
    assert client.post(f"/groups/{group_id}/delete").status_code == 403
    with configured.app_context():
        assert db.session.get(DisplayGroup, group_id).name == "Lobby"


def test_anonymous_is_redirected(configured, client):
    assert client.get("/groups/").status_code == 302


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_editor_and_admin_can_manage(configured, client, role):
    if role == "admin":
        login(client, "admin")
    else:
        _as(client, configured, role)
    assert client.get("/groups/").status_code == 200
    assert client.get("/groups/new").status_code == 200
    resp = client.post("/groups/new", data={"name": "Lobby"})
    assert resp.status_code == 302
    with configured.app_context():
        assert DisplayGroup.query.filter_by(name="Lobby").count() == 1


# --- CRUD -------------------------------------------------------------------------------

def test_empty_state(admin_client):
    html = admin_client.get("/groups/").get_data(as_text=True)
    assert "No groups yet" in html


def test_create_edit_and_list(configured, admin_client):
    a = _display(configured, "Front desk")
    b = _display(configured, "Hall")
    form = admin_client.get("/groups/new").get_data(as_text=True)
    assert "Front desk" in form and "Hall" in form

    resp = admin_client.post("/groups/new", data={"name": "  Lobby ", "description": "Ground floor",
                                                  "members": [a, b]}, follow_redirects=True)
    assert resp.status_code == 200
    with configured.app_context():
        group = DisplayGroup.query.one()
        assert (group.name, group.description) == ("Lobby", "Ground floor")
        assert {d.id for d in group.displays} == {a, b}
        group_id = group.id

    html = admin_client.get("/groups/").get_data(as_text=True)
    assert "Lobby" in html and "Ground floor" in html

    edit = admin_client.get(f"/groups/{group_id}/edit").get_data(as_text=True)
    assert f'value="{a}" checked' in edit

    resp = admin_client.post(f"/groups/{group_id}/edit",
                             data={"name": "Entrance", "description": "", "members": [b]})
    assert resp.status_code == 302
    with configured.app_context():
        group = db.session.get(DisplayGroup, group_id)
        assert (group.name, group.description) == ("Entrance", None)
        assert [d.id for d in group.displays] == [b]
        assert db.session.get(Display, a).group_id is None

    # No members at all also saves (empty checkbox list).
    admin_client.post(f"/groups/{group_id}/edit", data={"name": "Entrance"})
    with configured.app_context():
        assert db.session.get(DisplayGroup, group_id).displays == []


def test_name_is_required_and_unique_case_insensitive(configured, admin_client):
    group_id = _group(configured, "Lobby")
    other = _group(configured, "Hall")
    resp = admin_client.post("/groups/new", data={"name": "lobby"})
    assert resp.status_code == 200 and "already has this name" in resp.get_data(as_text=True)
    resp = admin_client.post("/groups/new", data={"name": ""})
    assert resp.status_code == 200
    resp = admin_client.post(f"/groups/{other}/edit", data={"name": "LOBBY"})
    assert "already has this name" in resp.get_data(as_text=True)
    # Renaming a group to its own name (different case) is fine.
    assert admin_client.post(f"/groups/{group_id}/edit", data={"name": "LOBBY"}).status_code == 302
    with configured.app_context():
        assert DisplayGroup.query.count() == 2


def test_unknown_member_and_group_are_rejected(configured, admin_client):
    resp = admin_client.post("/groups/new", data={"name": "Lobby", "members": [999]})
    assert resp.status_code == 200
    with configured.app_context():
        assert DisplayGroup.query.count() == 0
    assert admin_client.get("/groups/999/edit").status_code == 404
    assert admin_client.post("/groups/999/delete").status_code == 404


# --- Membership -------------------------------------------------------------------------

def test_membership_moves_display_and_publishes(configured, admin_client, listener):
    first = _group(configured, "Lobby")
    display_id = _display(configured, "Front desk", group_id=first)
    _drain(listener)
    resp = admin_client.post("/groups/new", data={"name": "Hall", "members": [display_id]},
                             follow_redirects=True)
    assert "Moved 1 display from other groups" in resp.get_data(as_text=True)
    assert ("manifest", {}) in _drain(listener)
    with configured.app_context():
        display = db.session.get(Display, display_id)
        assert display.group.name == "Hall"
        assert db.session.get(DisplayGroup, first).displays == []


def test_saving_without_changes_flashes_no_move(configured, admin_client):
    group_id = _group(configured, "Lobby")
    display_id = _display(configured, "Front desk", group_id=group_id)
    resp = admin_client.post(f"/groups/{group_id}/edit",
                             data={"name": "Lobby", "members": [display_id]},
                             follow_redirects=True)
    assert "Moved" not in resp.get_data(as_text=True)


# --- Delete -----------------------------------------------------------------------------

def test_delete_keeps_members_removes_schedules_and_clears_emergencies(configured, admin_client,
                                                                      listener):
    group_id = _group(configured, "Lobby")
    display_id = _display(configured, "Front desk", group_id=group_id)
    with configured.app_context():
        playlist = Playlist(name="Morning")
        db.session.add(playlist)
        db.session.flush()
        db.session.add(Schedule(name="Mornings", playlist_id=playlist.id, scope="group",
                                group_id=group_id))
        db.session.add(Schedule(name="Everywhere", playlist_id=playlist.id, scope="all"))
        db.session.add(EmergencyMessage(title="Drill", scope="group", group_id=group_id,
                                        target_label="Group Lobby"))
        db.session.commit()

    html = admin_client.get("/groups/").get_data(as_text=True)
    assert "Members are kept; 1 schedule(s) are removed." in html

    _drain(listener)
    resp = admin_client.post(f"/groups/{group_id}/delete", follow_redirects=True)
    assert "1 schedule removed" in resp.get_data(as_text=True)
    assert ("manifest", {}) in _drain(listener)
    with configured.app_context():
        assert db.session.get(DisplayGroup, group_id) is None
        display = db.session.get(Display, display_id)
        assert display is not None and display.group_id is None
        assert [s.name for s in Schedule.query.all()] == ["Everywhere"]
        msg = EmergencyMessage.query.one()
        assert msg.cleared_at is not None
        assert msg.clear_reason == "target_deleted"
        assert (msg.target_label, msg.scope, msg.group_id) == ("Group Lobby", "group", None)


# --- Display form -----------------------------------------------------------------------

def test_display_form_group_select(configured, admin_client):
    group_id = _group(configured, "Lobby")
    display_id = _display(configured, "Front desk")
    html = admin_client.get(f"/displays/{display_id}/edit").get_data(as_text=True)
    assert 'name="group"' in html and "No group" in html and "Lobby" in html
    resp = admin_client.post(f"/displays/{display_id}/edit",
                             data={"name": "Front desk", "playlist": 0, "group": group_id})
    assert resp.status_code == 302
    with configured.app_context():
        assert db.session.get(Display, display_id).group_id == group_id
    admin_client.post(f"/displays/{display_id}/edit",
                      data={"name": "Front desk", "playlist": 0, "group": 0})
    with configured.app_context():
        assert db.session.get(Display, display_id).group_id is None
