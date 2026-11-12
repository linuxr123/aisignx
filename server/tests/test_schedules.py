"""Schedules admin: role matrix, form rules, CHECK constraints, deletes, live updates,
badges, the display pages and the migration."""
import queue
from datetime import date, datetime, time, timezone

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from aisignx.extensions import db
from aisignx.models import (Display, DisplayGroup, EmergencyMessage, Media, Playlist,
                            PlaylistItem, Schedule)
from aisignx.player.broker import get_broker
from aisignx.player.manifest import build_manifest

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


def _playlist(name: str, playable: bool = True) -> Playlist:
    playlist = Playlist(name=name)
    if playable:
        media = Media(name=f"{name} page", media_type="web", url="https://example.com/" + name,
                      default_duration=10)
        playlist.items.append(PlaylistItem(media=media, position=0))
    db.session.add(playlist)
    return playlist


@pytest.fixture
def world(app, configured, paired_display):
    """Two playable playlists, a group holding the paired display, and an unpaired display."""
    display_id, _ = paired_display
    with app.app_context():
        morning, lunch = _playlist("Morning"), _playlist("Lunch")
        group = DisplayGroup(name="Lobby screens")
        db.session.add(group)
        db.session.flush()
        display = db.session.get(Display, display_id)
        display.group_id = group.id
        other = Display(name="Back office")
        db.session.add(other)
        db.session.commit()
        return {"display": display_id, "other": other.id, "group": group.id,
                "morning": morning.id, "lunch": lunch.id}


def _add_schedule(**kw) -> int:
    values = dict(name="S", scope="all", days_mask=0b1111111, priority=0, enabled=True)
    values.update(kw)
    schedule = Schedule(**values)
    db.session.add(schedule)
    db.session.commit()
    return schedule.id


def _form(world, **overrides) -> dict:
    data = {"name": "Breakfast", "playlist": world["morning"], "target": "all",
            "days": ["0", "1", "2", "3", "4"], "start_time": "08:00", "end_time": "11:30",
            "start_date": "", "end_date": "", "priority": "5", "enabled": "y"}
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


# --- Role matrix ------------------------------------------------------------------------

def test_viewer_can_read_but_not_change(app, client, world):
    with app.app_context():
        sid = _add_schedule(name="Mornings", playlist_id=world["morning"])
    _as(client, app, "viewer")
    page = client.get("/schedules/")
    assert page.status_code == 200 and "Mornings" in page.get_data(as_text=True)
    assert "New schedule" not in page.get_data(as_text=True)
    assert client.get("/schedules/new").status_code == 403
    assert client.get(f"/schedules/{sid}/edit").status_code == 403
    assert client.post("/schedules/new", data=_form(world)).status_code == 403
    for action in ("edit", "toggle", "duplicate", "delete"):
        assert client.post(f"/schedules/{sid}/{action}").status_code == 403
    with app.app_context():
        assert Schedule.query.count() == 1


def test_editor_creates_edits_toggles_duplicates_and_deletes(app, client, world):
    _as(client, app, "editor")
    assert client.get("/schedules/new").status_code == 200
    resp = client.post("/schedules/new", data=_form(world, target=f"g:{world['group']}"))
    assert resp.status_code == 302
    with app.app_context():
        s = Schedule.query.one()
        assert (s.scope, s.group_id, s.display_id) == ("group", world["group"], None)
        assert s.days_mask == 0b0011111 and s.days_label == "Mon–Fri"
        assert (s.start_time, s.end_time, s.priority) == (time(8), time(11, 30), 5)
        sid = s.id
    assert client.get(f"/schedules/{sid}/edit").status_code == 200

    assert client.post(f"/schedules/{sid}/toggle").status_code == 302
    with app.app_context():
        assert db.session.get(Schedule, sid).enabled is False

    resp = client.post(f"/schedules/{sid}/duplicate")
    assert resp.status_code == 302 and "/edit" in resp.headers["Location"]
    with app.app_context():
        copy = Schedule.query.filter(Schedule.id != sid).one()
        assert copy.name == "Breakfast (copy)" and copy.enabled is False
        assert copy.group_id == world["group"] and copy.start_time == time(8)

    resp = client.post(f"/schedules/{sid}/delete", follow_redirects=True)
    assert b"Deleted schedule Breakfast." in resp.data
    with app.app_context():
        assert db.session.get(Schedule, sid) is None


def test_overnight_and_all_day_are_saved(admin_client, app, world):
    admin_client.post("/schedules/new", data=_form(world, name="Night", start_time="22:00",
                                                   end_time="02:00"))
    admin_client.post("/schedules/new", data=_form(world, name="Allday", all_day="y"))
    with app.app_context():
        night = Schedule.query.filter_by(name="Night").one()
        assert night.is_overnight and night.time_label == "22:00 → 02:00 (+1 day)"
        allday = Schedule.query.filter_by(name="Allday").one()
        assert allday.start_time is None and allday.end_time is None


# --- Form rules -------------------------------------------------------------------------

@pytest.mark.parametrize("overrides", [
    {"days": None},                                     # no days
    {"start_time": "09:00", "end_time": "09:00"},       # start == end
    {"end_time": ""},                                   # a single time
    {"start_time": ""},
    {"start_date": "2026-12-10", "end_date": "2026-12-01"},
    {"priority": "101"},
    {"priority": "-1"},
    {"target": "d:999"},
    {"target": "g:999"},
    {"target": "everything"},
    {"target": "d:1; DROP"},
    {"playlist": "999"},
    {"playlist": "abc"},
    {"name": ""},
])
def test_form_rejects(admin_client, app, world, overrides):
    resp = admin_client.post("/schedules/new", data=_form(world, **overrides))
    assert resp.status_code == 200  # form shown again with errors
    assert "invalid-feedback" in resp.get_data(as_text=True)
    with app.app_context():
        assert Schedule.query.count() == 0


def test_edit_clears_dates_and_times(admin_client, app, world):
    with app.app_context():
        sid = _add_schedule(playlist_id=world["morning"], start_time=time(8), end_time=time(9),
                            start_date=date(2026, 1, 1), end_date=date(2026, 12, 31))
    page = admin_client.get(f"/schedules/{sid}/edit").get_data(as_text=True)
    assert 'value="08:00"' in page and 'value="2026-12-31"' in page
    resp = admin_client.post(f"/schedules/{sid}/edit",
                             data=_form(world, name="S", all_day="y", start_time="",
                                        end_time="", start_date="", end_date=""))
    assert resp.status_code == 302
    with app.app_context():
        s = db.session.get(Schedule, sid)
        assert (s.start_time, s.end_time, s.start_date, s.end_date) == (None, None, None, None)


@pytest.mark.parametrize("bad", [
    {"scope": "everywhere"},
    {"scope": "display"},                                # no display_id
    {"scope": "all", "group_id": "GROUP"},
    {"days_mask": 0},
    {"days_mask": 128},
    {"start_time": time(8)},                             # end_time missing
    {"start_time": time(8), "end_time": time(8)},
    {"start_date": date(2026, 2, 1), "end_date": date(2026, 1, 1)},
    {"priority": 101},
])
def test_check_constraints(app, world, bad):
    with app.app_context():
        values = dict(name="Bad", playlist_id=world["morning"], scope="all", days_mask=1)
        values.update({k: (world["group"] if v == "GROUP" else v) for k, v in bad.items()})
        db.session.add(Schedule(**values))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


# --- Deletes ----------------------------------------------------------------------------

def test_playlist_delete_refused_while_scheduled(admin_client, app, world):
    with app.app_context():
        sid = _add_schedule(name="Mornings", playlist_id=world["morning"])
    resp = admin_client.post(f"/playlists/{world['morning']}/delete", follow_redirects=True)
    assert b"used by schedule(s) Mornings" in resp.data
    with app.app_context():
        assert db.session.get(Playlist, world["morning"]) is not None
        db.session.delete(db.session.get(Schedule, sid))
        db.session.commit()
    admin_client.post(f"/playlists/{world['morning']}/delete")
    with app.app_context():
        assert db.session.get(Playlist, world["morning"]) is None


def test_playlist_delete_refused_while_live_emergency_uses_it(admin_client, app, world):
    with app.app_context():
        msg = EmergencyMessage(title="Fire drill", level="critical", scope="all",
                               target_label="All screens", playlist_id=world["lunch"])
        db.session.add(msg)
        db.session.commit()
        msg_id = msg.id
    resp = admin_client.post(f"/playlists/{world['lunch']}/delete", follow_redirects=True)
    assert b"Fire drill" in resp.data
    with app.app_context():
        assert db.session.get(Playlist, world["lunch"]) is not None
        db.session.get(EmergencyMessage, msg_id).cleared_at = datetime.now(timezone.utc)
        db.session.commit()
    admin_client.post(f"/playlists/{world['lunch']}/delete")
    with app.app_context():
        assert db.session.get(Playlist, world["lunch"]) is None
        assert db.session.get(EmergencyMessage, msg_id).playlist_id is None  # history kept


def test_display_delete_cascades_its_schedules(admin_client, app, world):
    with app.app_context():
        own = _add_schedule(playlist_id=world["morning"], scope="display",
                            display_id=world["display"])
        everyone = _add_schedule(playlist_id=world["morning"])
    page = admin_client.get(f"/displays/{world['display']}").get_data(as_text=True)
    assert "also removes 1 schedule(s)" in page
    admin_client.post(f"/displays/{world['display']}/delete")
    with app.app_context():
        assert db.session.get(Schedule, own) is None
        assert db.session.get(Schedule, everyone) is not None


def test_group_delete_cascades_its_schedules(app, world):
    with app.app_context():
        sid = _add_schedule(playlist_id=world["morning"], scope="group", group_id=world["group"])
        db.session.delete(db.session.get(DisplayGroup, world["group"]))
        db.session.commit()
        db.session.expire_all()
        assert db.session.get(Schedule, sid) is None
        assert db.session.get(Display, world["display"]).group_id is None


# --- Live updates -----------------------------------------------------------------------

@pytest.fixture
def listener(app):
    with app.app_context():
        broker = get_broker()
        sub = broker.subscribe(999_999)
    yield sub
    broker.unsubscribe(sub)


def test_schedule_commit_publishes_manifest(admin_client, app, world, listener):
    _drain(listener)
    admin_client.post("/schedules/new", data=_form(world))
    assert ("manifest", {}) in _drain(listener)
    with app.app_context():
        sid = Schedule.query.one().id
    admin_client.post(f"/schedules/{sid}/toggle")
    assert ("manifest", {}) in _drain(listener)
    admin_client.post(f"/schedules/{sid}/delete")
    assert ("manifest", {}) in _drain(listener)


def test_manifest_source_and_version_follow_the_clock(app, world):
    with app.app_context():
        sid = _add_schedule(name="Lunch menu", playlist_id=world["lunch"],
                            start_time=time(12), end_time=time(14))
        db.session.get(Display, world["display"]).playlist_id = world["morning"]
        db.session.commit()
    with app.test_request_context():
        display = db.session.get(Display, world["display"])
        at_ten = build_manifest(display, datetime(2026, 11, 11, 10, tzinfo=timezone.utc))
        at_one = build_manifest(display, datetime(2026, 11, 11, 13, tzinfo=timezone.utc))
    assert at_ten["source"] == {"type": "default", "id": None, "name": None}
    assert at_ten["playlist"]["id"] == world["morning"]
    assert at_one["source"] == {"type": "schedule", "id": sid, "name": "Lunch menu"}
    assert at_one["playlist"]["id"] == world["lunch"]
    assert at_ten["version"] != at_one["version"]
    assert at_ten["emergency"] is None


# --- Pages ------------------------------------------------------------------------------

def test_playing_and_shadowed_badges(admin_client, app, world):
    with app.app_context():
        winner = _add_schedule(name="Winner", playlist_id=world["morning"], priority=50)
        loser = _add_schedule(name="Loser", playlist_id=world["lunch"], priority=10)
        empty = _playlist("Empty", playable=False)
        db.session.commit()
        _add_schedule(name="Hollow", playlist_id=empty.id)
    page = admin_client.get("/schedules/").get_data(as_text=True)
    assert "Times are in UTC" in page
    assert f'data-schedule-playing="{winner}">Playing on 1<' in page  # only one is paired
    assert 'data-schedule-badge="shadowed">Shadowed<' in page
    assert f'data-schedule-playing="{loser}"' not in page
    assert 'data-schedule-badge="empty"' in page
    assert page.index("Winner") < page.index("Loser")  # priority desc


def test_display_pages_show_now_playing_group_and_week(admin_client, app, world):
    with app.app_context():
        _add_schedule(name="Mornings", playlist_id=world["morning"], start_time=time(8),
                      end_time=time(12))
    index = admin_client.get("/displays/").get_data(as_text=True)
    assert "Now playing" in index and 'data-source=' in index
    page = admin_client.get(f"/displays/{world['display']}").get_data(as_text=True)
    assert "Lobby screens" in page and "Next 7 days" in page
    assert "08:00&ndash;12:00" in page and "(schedule Mornings)" in page
    assert "Default playlist" in page


def test_display_edit_sets_and_clears_group(admin_client, app, world):
    page = admin_client.get(f"/displays/{world['other']}/edit").get_data(as_text=True)
    assert "Default playlist" in page and "No group" in page and "Lobby screens" in page
    admin_client.post(f"/displays/{world['other']}/edit",
                      data={"name": "Back office", "playlist": 0, "group": world["group"]})
    with app.app_context():
        assert db.session.get(Display, world["other"]).group_id == world["group"]
    admin_client.post(f"/displays/{world['other']}/edit",
                      data={"name": "Back office", "playlist": 0, "group": 0})
    with app.app_context():
        assert db.session.get(Display, world["other"]).group_id is None


def test_dashboard_and_nav(admin_client, app, world):
    with app.app_context():
        _add_schedule(playlist_id=world["morning"])
        _add_schedule(playlist_id=world["morning"], enabled=False)
    page = admin_client.get("/dashboard").get_data(as_text=True)
    assert "Enabled schedules" in page and "Groups" in page
    for href in ('href="/schedules/"', 'href="/groups/"', 'href="/emergency/"'):
        assert href in page
    assert "Emergency active" not in page


# --- Migration --------------------------------------------------------------------------

def test_migration_downgrade_and_upgrade(app):
    with app.app_context():
        downgrade(revision="6432f461876d")
        tables = inspect(db.engine).get_table_names()
        assert "schedules" not in tables and "display_groups" not in tables
        assert "group_id" not in {c["name"] for c in inspect(db.engine).get_columns("displays")}
        upgrade()
        tables = inspect(db.engine).get_table_names()
        assert {"schedules", "display_groups", "emergency_messages"} <= set(tables)
