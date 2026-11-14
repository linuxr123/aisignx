"""Audit log: the call sites in the admin pages, the viewer, its CSV export and retention."""
import json
from datetime import datetime, timedelta, timezone

import pytest

from aisignx.extensions import db
from aisignx.models import (AuditLog, Display, DisplayGroup, Media, Playlist, PlaylistItem,
                            Setting, User)
from aisignx.timeutil import utcnow

from .conftest import PASSWORD, login, make_user, run_job

API = "/api/player/v1"


def _rows(app, action=None) -> list[AuditLog]:
    with app.app_context():
        query = AuditLog.query.order_by(AuditLog.id)
        if action:
            query = query.filter(AuditLog.action == action)
        rows = query.all()
        for r in rows:
            db.session.expunge(r)
        return rows


def _one(app, action) -> AuditLog:
    rows = _rows(app, action)
    assert len(rows) == 1, (action, [r.action for r in _rows(app)])
    return rows[0]


def _detail(row) -> dict:
    return json.loads(row.detail) if row.detail else {}


def _check(row, target_type, target_id, username="admin", via="web"):
    assert (row.target_type, row.target_id) == (target_type, target_id)
    assert (row.username, row.via) == (username, via)
    assert row.ip == "127.0.0.1"


def _add(app, **kw) -> int:
    values = dict(ts=utcnow(), action="x.test", username="admin", via="web")
    values.update(kw)
    with app.app_context():
        row = AuditLog(**values)
        db.session.add(row)
        db.session.commit()
        return row.id


# --- Call sites -------------------------------------------------------------------------

def test_setup_records_admin_created(app, client):
    client.post("/setup/", data={"org_name": "Acme", "timezone": "UTC", "username": "boss",
                                 "email": "", "password": PASSWORD, "confirm": PASSWORD})
    row = _one(app, "setup.admin_created")
    with app.app_context():
        boss = User.query.filter_by(username="boss").one().id
    _check(row, "user", boss, username="boss")
    assert row.user_id == boss and _detail(row)["org_name"] == "Acme"


def test_user_actions(app, admin_client):
    admin_client.post("/users/new", data={"username": "carol", "email": "c@example.com",
                                          "role": "editor", "password": PASSWORD})
    with app.app_context():
        carol = User.query.filter_by(username="carol").one().id
    _check(_one(app, "user.create"), "user", carol)

    admin_client.post(f"/users/{carol}/edit", data={"email": "", "role": "viewer",
                                                    "password": "another-long-password"})
    row = _one(app, "user.update")
    _check(row, "user", carol)
    assert _detail(row) == {"changed": {"role": ["editor", "viewer"], "active": [True, False],
                                        "email": True}}
    _check(_one(app, "user.password_reset"), "user", carol)
    assert all("another-long" not in (r.detail or "") for r in _rows(app))

    admin_client.post(f"/users/{carol}/delete")
    row = _one(app, "user.delete")
    _check(row, "user", carol)
    assert row.target_name == "carol"


def test_user_edit_without_changes_records_nothing(app, admin_client):
    uid = make_user(app, "dave", "viewer")
    admin_client.post(f"/users/{uid}/edit", data={"email": "", "role": "viewer", "active": "y"})
    assert _rows(app, "user.update") == [] and _rows(app, "user.password_reset") == []


def test_settings_update_lists_changed_keys(app, admin_client):
    admin_client.post("/settings/", data={"org_name": "New name", "timezone": "UTC",
                                          "default_duration": "10"})
    row = _one(app, "settings.update")
    assert _detail(row) == {"changed": ["org_name"]} and row.target_type is None


def test_display_actions(app, admin_client, paired_display):
    did = paired_display[0]
    admin_client.post(f"/displays/{did}/edit", data={
        "name": "Lobby 2", "playlist": 0, "group": 0, "orientation": 0, "fit_override": "",
        "volume": 50, "show_offline_banner": "y", "allow_input": "y", "unlock_pin": "2468"})
    row = _one(app, "display.update")
    _check(row, "display", did)
    assert set(_detail(row)["changed"]) == {"name", "volume", "allow_input", "unlock_pin"}
    assert "2468" not in row.detail and row.target_name == "Lobby 2"

    for action in ("reload", "identify"):  # not connected: still recorded, not delivered
        admin_client.post(f"/displays/{did}/{action}")
        row = _one(app, f"display.{action}")
        _check(row, "display", did)
        assert _detail(row) == {"delivered": False}

    admin_client.post(f"/displays/{did}/screenshot")
    _check(_one(app, "display.screenshot_request"), "display", did)
    admin_client.post(f"/displays/{did}/unpair")
    _check(_one(app, "display.unpair"), "display", did)
    admin_client.post(f"/displays/{did}/delete")
    row = _one(app, "display.delete")
    _check(row, "display", did)
    assert _detail(row) == {"schedules_removed": 0, "emergencies_cleared": 0}


def test_display_pair_via_admin_route(app, admin_client):
    player = app.test_client()
    code = player.post(f"{API}/pair", json={}).get_json()["code"]
    admin_client.post("/displays/pair", data={"code": code, "name": "Hall", "playlist": "0",
                                              "pair_as": "0"})
    with app.app_context():
        did = Display.query.filter_by(name="Hall").one().id
    row = _one(app, "display.pair")
    _check(row, "display", did)
    assert _detail(row) == {"replaced": False, "playlist_id": None}
    assert code not in (row.detail or "")


def test_group_actions_use_type_group(app, admin_client):
    admin_client.post("/groups/new", data={"name": "Lobby"})
    with app.app_context():
        gid = DisplayGroup.query.one().id
    _check(_one(app, "group.create"), "group", gid)
    admin_client.post(f"/groups/{gid}/edit", data={"name": "Entrance"})
    row = _one(app, "group.update")
    _check(row, "group", gid)
    assert row.target_name == "Entrance"
    admin_client.post(f"/groups/{gid}/delete")
    _check(_one(app, "group.delete"), "group", gid)


@pytest.fixture
def library(app):
    with app.app_context():
        items = [Media(name="Logo", media_type="image", default_duration=10),
                 Media(name="Menu", media_type="web", url="https://example.com",
                       default_duration=20)]
        db.session.add_all(items)
        db.session.commit()
        return [m.id for m in items]


def test_playlist_actions(app, admin_client, library):
    admin_client.post("/playlists/new", data={"name": "Main", "default_transition": "fade"})
    with app.app_context():
        pid = Playlist.query.one().id
    _check(_one(app, "playlist.create"), "playlist", pid)
    admin_client.post(f"/playlists/{pid}/edit", data={"name": "Main 2",
                                                      "default_transition": "fade"})
    assert _detail(_one(app, "playlist.update")) == {"changed": ["name"]}

    admin_client.post(f"/playlists/{pid}/items", data={"media_ids": library})
    row = _one(app, "playlist.item_add")
    _check(row, "playlist", pid)
    assert _detail(row) == {"media_ids": library, "added": 2}
    with app.app_context():
        items = [i.id for i in PlaylistItem.query.order_by(PlaylistItem.position)]

    admin_client.post(f"/playlists/{pid}/items/{items[0]}",
                      data={"duration": "7", "transition": "", "fit_mode": "fit"})
    assert _detail(_one(app, "playlist.item_update"))["duration"] == 7
    admin_client.post(f"/playlists/{pid}/items/{items[0]}/move", data={"direction": "down"})
    admin_client.post(f"/playlists/{pid}/reorder", json={"item_ids": items})
    assert [_detail(r) for r in _rows(app, "playlist.reorder")] == [
        {"item_id": items[0], "direction": "down"}, {"item_ids": items}]
    admin_client.post(f"/playlists/{pid}/items/{items[1]}/delete")
    assert _detail(_one(app, "playlist.item_remove"))["media"] == "Menu"
    admin_client.post(f"/playlists/{pid}/delete")
    _check(_one(app, "playlist.delete"), "playlist", pid)


def test_schedule_actions(app, admin_client, library):
    with app.app_context():
        playlist = Playlist(name="P")
        playlist.items.append(PlaylistItem(media_id=library[0], position=0))
        db.session.add(playlist)
        db.session.commit()
        pid = playlist.id
    form = {"name": "Breakfast", "playlist": pid, "target": "all", "days": ["0", "1"],
            "start_time": "08:00", "end_time": "11:30", "start_date": "", "end_date": "",
            "priority": "5", "enabled": "y"}
    assert admin_client.post("/schedules/new", data=form).status_code == 302
    row = _one(app, "schedule.create")
    sid = row.target_id
    _check(row, "schedule", sid)
    assert _detail(row)["playlist_id"] == pid
    admin_client.post(f"/schedules/{sid}/edit", data=dict(form, priority="6"))
    assert _detail(_one(app, "schedule.update"))["priority"] == 6
    admin_client.post(f"/schedules/{sid}/toggle")
    assert _detail(_one(app, "schedule.toggle")) == {"enabled": False}
    admin_client.post(f"/schedules/{sid}/delete")
    _check(_one(app, "schedule.delete"), "schedule", sid)


def test_emergency_actions(app, admin_client):
    send = {"title": "Fire drill", "message": "Leave.", "level": "critical", "target": "all",
            "playlist": 0, "show_text": "y"}
    admin_client.post("/emergency/send", data=send)
    row = _one(app, "emergency.send")
    _check(row, "emergency", row.target_id)
    assert _detail(row)["target"] and _detail(row)["level"] == "critical"
    admin_client.post(f"/emergency/{row.target_id}/clear")
    assert _detail(_one(app, "emergency.clear"))["reason"] == "manual"
    admin_client.post("/emergency/send", data=dict(send, title="Second"))
    admin_client.post("/emergency/clear-all")
    assert [c["title"] for c in _detail(_one(app, "emergency.clear_all"))["cleared"]] == [
        "Second"]


def test_failed_commit_leaves_no_audit_row(app, admin_client, monkeypatch):
    def boom():
        raise RuntimeError("disk full")

    monkeypatch.setattr(db.session, "commit", boom)
    with pytest.raises(RuntimeError):
        admin_client.post("/groups/new", data={"name": "Lobby"})
    monkeypatch.undo()
    with app.app_context():
        assert AuditLog.query.filter(AuditLog.action.startswith("group.")).count() == 0
        assert DisplayGroup.query.count() == 0


# --- Viewer -----------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_viewer_is_admin_only(configured, client, role):
    make_user(configured, "someone", role)
    login(client, "someone")
    assert client.get("/admin/audit/").status_code == 403
    assert client.get("/admin/audit/export.csv").status_code == 403
    assert client.post("/admin/audit/settings", data={"retention_days": "1"}).status_code == 403


def test_viewer_lists_and_filters(app, admin_client):
    _add(app, action="display.update", target_type="display", target_id=12,
         target_name="Lobby", detail=json.dumps({"changed": ["name"]}))
    _add(app, action="display.reload", target_type="display", target_id=13)
    _add(app, action="auth.login_failed", username="mallory")
    _add(app, action="group.update", target_type="displaygroup", target_id=4)

    def shown(**args):
        html = admin_client.get("/admin/audit/", query_string=args).get_data(as_text=True)
        return {a for a in ("display.update", "display.reload", "auth.login_failed",
                            "group.update") if f'title="{a}"' in html}

    assert len(shown()) == 4
    assert shown(target="display:12") == {"display.update"}
    assert shown(target="display") == {"display.update", "display.reload"}
    assert shown(target="group:4") == {"group.update"}  # old "displaygroup" rows match
    assert shown(action="display.") == {"display.update", "display.reload"}
    assert shown(action="display.reload") == {"display.reload"}
    assert shown(user="MALLORY") == {"auth.login_failed"}
    assert len(shown(target="bad target!")) == 4  # an invalid filter is ignored
    html = admin_client.get("/admin/audit/?target=display:12").get_data(as_text=True)
    assert '&#34;changed&#34;: [\n    &#34;name&#34;' in html or '"changed": [\n    "name"' in html


def test_date_filter_uses_org_timezone_and_inclusive_end(app, admin_client):
    with app.app_context():
        Setting.set("timezone", "Europe/Brussels")  # UTC+1 in March
        db.session.commit()
    _add(app, action="a.last_minute", ts=datetime(2026, 3, 9, 22, 59, tzinfo=timezone.utc))
    _add(app, action="a.next_day", ts=datetime(2026, 3, 9, 23, 0, tzinfo=timezone.utc))

    def shown(**args):
        html = admin_client.get("/admin/audit/", query_string=args).get_data(as_text=True)
        return {a for a in ("a.last_minute", "a.next_day") if f'title="{a}"' in html}

    assert shown(date_to="2026-03-09") == {"a.last_minute"}
    assert shown(date_from="2026-03-10") == {"a.next_day"}
    assert shown(date_from="2026-03-09", date_to="2026-03-10") == {"a.last_minute",
                                                                     "a.next_day"}
    assert shown(date_from="2026-03-11") == set()


def test_pagination(app, admin_client):
    with app.app_context():
        base = utcnow() + timedelta(days=1)  # newer than the login row
        db.session.add_all([AuditLog(ts=base - timedelta(seconds=i), action=f"x.n{i:02d}")
                            for i in range(51)])
        db.session.commit()
    first = admin_client.get("/admin/audit/").get_data(as_text=True)
    assert 'title="x.n00"' in first and 'title="x.n49"' in first
    assert 'title="x.n50"' not in first and "Older" in first
    second = admin_client.get("/admin/audit/?page=2").get_data(as_text=True)
    assert 'title="x.n50"' in second and 'title="x.n00"' not in second


def test_csv_export_streams_filters_and_escapes(app, admin_client):
    _add(app, action="display.update", target_type="display", target_id=1,
         target_name="=HYPERLINK(\"http://evil\")")
    _add(app, action="auth.login", target_type="user", target_id=1)
    resp = admin_client.get("/admin/audit/export.csv?action=display.")
    assert resp.status_code == 200 and resp.is_streamed
    assert resp.mimetype == "text/csv" and "attachment" in resp.headers["Content-Disposition"]
    text = resp.get_data().decode("utf-8")
    assert text.startswith("﻿time_utc,")
    assert "'=HYPERLINK" in text and "auth.login" not in text
    assert len(text.strip().splitlines()) == 2


def test_history_links_for_admin_only(app, client, configured, paired_display):
    did = paired_display[0]
    login(client, "admin")
    href = f'href="/admin/audit/?target=display:{did}"'
    assert href in client.get(f"/displays/{did}").get_data(as_text=True)
    assert href in client.get(f"/displays/{did}/edit").get_data(as_text=True)
    uid = make_user(configured, "ed", "editor")
    assert "/admin/audit/?target=user:" in client.get(f"/users/{uid}/edit").get_data(
        as_text=True)
    editor = configured.test_client()
    login(editor, "ed")
    assert "/admin/audit/?target" not in editor.get(f"/displays/{did}").get_data(as_text=True)


# --- Retention --------------------------------------------------------------------------

def test_retention_setting(app, admin_client):
    page = admin_client.get("/admin/audit/").get_data(as_text=True)
    assert 'value="365"' in page
    admin_client.post("/admin/audit/settings", data={"retention_days": "30"})
    with app.app_context():
        assert Setting.get("audit.retention_days") == "30"
    assert _detail(_one(app, "audit.settings")) == {"retention_days": [365, 30]}
    admin_client.post("/admin/audit/settings", data={"retention_days": "0"})
    admin_client.post("/admin/audit/settings", data={"retention_days": "5000"})
    with app.app_context():
        assert Setting.get("audit.retention_days") == "0"
    assert len(_rows(app, "audit.settings")) == 2


def test_purge_respects_retention(app, configured):
    old, new = utcnow() - timedelta(days=40), utcnow() - timedelta(days=10)
    _add(app, action="x.old", ts=old)
    _add(app, action="x.new", ts=new)
    with app.app_context():
        Setting.set("audit.retention_days", "0")
        db.session.commit()
    run_job(app, "audit.purge")
    assert len(_rows(app)) == 2  # 0 keeps everything
    with app.app_context():
        Setting.set("audit.retention_days", "30")
        db.session.commit()
    run_job(app, "audit.purge")
    assert [r.action for r in _rows(app)] == ["x.new"]
