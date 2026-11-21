"""Fleet pages: choose which screens to update and watch them, edit several screens at once, live regions."""
import io
import json
import re

import pytest

from aisignx.displays import updates
from aisignx.extensions import db
from aisignx.models import AuditLog, DeviceCommand, Display, Download
from aisignx.player.auth import hash_token
from aisignx.timeutil import utcnow

from .conftest import login, make_user


def _download(app, kind, filename, version):
    from aisignx.downloads.routes import downloads_dir
    with app.app_context():
        data = (filename + version).encode() * 3
        name = "f" + re.sub(r"\W", "", filename + version)[:20]
        (downloads_dir() / name).write_bytes(data)
        import hashlib
        d = Download(kind=kind, filename=filename, stored_name=name, version=version, size=len(data), sha256=hashlib.sha256(data).hexdigest())
        db.session.add(d)
        db.session.commit()


def _screen(app, name, shell, version, allow_update=True, can_update=True, ua="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Electron/30"):
    with app.app_context():
        d = Display(name=name, token_hash=hash_token(name * 10), paired_at=utcnow(), agent_allow_update=allow_update)
        d.diagnostics = json.dumps({"ua": ua, "shell": {"name": shell, "version": version},
                                    "agent": {"version": version, "can_update": can_update, "can_restart": True}})
        db.session.add(d)
        db.session.commit()
        return d.id


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _csrf(client, path="/displays/updates"):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def test_the_updates_page_lists_each_screen_with_its_state(app, admin):
    _download(app, "windows-player", "AiSignX Player Setup 3.5.0.exe", "3.5.0")
    old = _screen(app, "Lobby", "electron", "3.4.0")
    cur = _screen(app, "Cafe", "electron", "3.5.0")
    off = _screen(app, "Hall", "electron", "3.3.0", allow_update=False)
    _screen(app, "Browser only", "", "")
    with app.app_context():
        rows = {r["display"].name: r for r in updates.rows(Display.query.all())}
    assert set(rows) == {"Lobby", "Cafe", "Hall"}                                    # the browser screen has no app
    assert rows["Lobby"]["status"] == "outdated" and rows["Lobby"]["can_push"]
    assert rows["Cafe"]["status"] == "current" and not rows["Cafe"]["can_push"]
    assert rows["Hall"]["status"] == "outdated" and not rows["Hall"]["can_push"] and "switched off" in rows["Hall"]["reason"]
    page = admin.get("/displays/updates").get_data(as_text=True)
    assert "Lobby" in page and "Update available" in page and "Up to date" in page and 'data-live="updates"' in page


def test_pushing_to_the_ticked_screens_only_and_the_status_follows_the_command(app, admin):
    _download(app, "windows-player", "AiSignX Player Setup 3.5.0.exe", "3.5.0")
    a = _screen(app, "A", "electron", "3.4.0")
    b = _screen(app, "B", "electron", "3.4.0")
    c = _screen(app, "C", "electron", "3.3.0", allow_update=False)
    tok = _csrf(admin)
    r = admin.post("/displays/updates/push", data={"csrf_token": tok, "display_id": [a, c]}, follow_redirects=True)
    text = r.get_data(as_text=True)
    assert "Update sent to 1 screen" in text and "C: " in text and "switched off" in text           # C is refused with its reason
    with app.app_context():
        cmds = DeviceCommand.query.all()
        assert [(m.display_id, m.action) for m in cmds] == [(a, "update_app")]                    # B was not ticked: untouched
        assert AuditLog.query.filter_by(action="display.agent_command").count() == 1
        rows = {x["display"].name: x for x in updates.rows(Display.query.all())}
        assert rows["A"]["status"] == "queued" and rows["B"]["status"] == "outdated"
        cmd = cmds[0]
        cmd.state = "delivered"
        db.session.commit()
        assert {x["display"].name: x["status"] for x in updates.rows(Display.query.all())}["A"] == "installing"
        cmd.state, cmd.result = "failed", "the installer is not signed by the pinned certificate"
        db.session.commit()
        rowa = {x["display"].name: x for x in updates.rows(Display.query.all())}["A"]
        assert rowa["status"] == "failed" and "pinned certificate" in rowa["result"] and rowa["can_push"]       # can be tried again
        cmd.state = "done"
        shell = json.loads(db.session.get(Display, a).diagnostics)
        shell["shell"]["version"] = "3.5.0"
        db.session.get(Display, a).diagnostics = json.dumps(shell)
        db.session.commit()
        assert {x["display"].name: x["status"] for x in updates.rows(Display.query.all())}["A"] == "done"
    assert admin.post("/displays/updates/push", data={"csrf_token": tok}, follow_redirects=True).get_data(as_text=True).count("Tick at least one screen") == 1


def test_only_admins_see_the_updates_page(app, configured):
    make_user(app, "ed", "editor")
    ed = app.test_client()
    login(ed, "ed")
    assert ed.get("/displays/updates").status_code == 403
    assert ed.post("/displays/updates/push", data={"display_id": [1]}).status_code in (400, 403)
    assert app.test_client().get("/displays/updates").status_code in (302, 401)


def test_the_list_offers_checkboxes_and_the_ticked_screens_reach_the_bulk_and_update_pages(app, admin):
    _download(app, "windows-player", "AiSignX Player Setup 3.5.0.exe", "3.5.0")
    a = _screen(app, "A", "electron", "3.4.0")
    b = _screen(app, "B", "electron", "3.4.0")
    page = admin.get("/displays/").get_data(as_text=True)
    assert 'data-bulk-one' in page and "Edit selected together" in page
    assert f'name="id" value="{a}"' in page
    up = admin.get(f"/displays/updates?id={b}").get_data(as_text=True)
    assert re.search(rf'value="{b}"[^>]*checked', up) and not re.search(rf'value="{a}"[^>]*checked', up)


def test_bulk_edit_changes_only_what_is_filled_in_on_the_ticked_screens(app, admin):
    a = _screen(app, "A", "electron", "3.4.0")
    b = _screen(app, "B", "android", "3.0.6", ua="Mozilla/5.0 (Linux; Android 13)")
    c = _screen(app, "C", "electron", "3.4.0")
    from aisignx.models import Playlist
    with app.app_context():
        pl = Playlist(name="Menu")
        db.session.add(pl)
        db.session.commit()
        pid = pl.id
        db.session.get(Display, a).volume = 40
        db.session.commit()
    page = admin.get(f"/displays/bulk?id={a}&id={b}")
    assert page.status_code == 200 and b"Edit 2 displays" in page.data and b"No change" in page.data
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True)).group(1)
    data = {"csrf_token": tok, "id": [a, b], "apply": "1", "playlist": str(pid), "group": "", "orientation": "90", "fit_override": "",
            "volume": "", "show_offline_banner": "0", "resume_after_min": "10", "agent_allow_reboot": "1", "agent_allow_logs": "", "allow_input": "0"}
    r = admin.post("/displays/bulk", data=data, follow_redirects=True)
    assert r.status_code == 200 and b"2 of 2 screens changed" in r.data
    with app.app_context():
        for did in (a, b):
            d = db.session.get(Display, did)
            assert d.playlist_id == pid and d.orientation == 90 and d.show_offline_banner is False and d.resume_after_s == 600
            assert d.agent_allow_reboot is True and d.allow_input is False
        assert db.session.get(Display, a).volume == 40                          # untouched: "no change"
        other = db.session.get(Display, c)
        assert other.playlist_id is None and other.orientation == 0              # not ticked: untouched
        assert AuditLog.query.filter_by(action="display.update").count() == 2
    again = admin.post("/displays/bulk", data=data, follow_redirects=True)
    assert b"0 of 2 screens changed" in again.data                               # nothing new to change
    bad = admin.post("/displays/bulk", data={**data, "volume": "500"})
    assert bad.status_code == 200 and b"changed" not in bad.data


def test_an_editor_can_bulk_edit_the_general_settings_but_never_the_admin_switches(app, configured):
    a = _screen(app, "A", "electron", "3.4.0")
    make_user(app, "ed", "editor")
    ed = app.test_client()
    login(ed, "ed")
    page = ed.get(f"/displays/bulk?id={a}")
    assert page.status_code == 200 and b"agent_allow_reboot" not in page.data
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True)).group(1)
    ed.post("/displays/bulk", data={"csrf_token": tok, "id": [a], "apply": "1", "playlist": "", "group": "", "orientation": "", "fit_override": "",
                                    "volume": "55", "show_offline_banner": "", "agent_allow_reboot": "1", "allow_input": "1"})
    with app.app_context():
        d = db.session.get(Display, a)
        assert d.volume == 55 and d.agent_allow_reboot is False and d.allow_input is False     # the admin switches were ignored
    assert ed.get("/displays/bulk").status_code == 302                                           # nothing ticked: back to the list


def test_the_detail_page_marks_the_parts_that_refresh_by_themselves(app, admin):
    a = _screen(app, "A", "electron", "3.4.0")
    page = admin.get(f"/displays/{a}").get_data(as_text=True)
    for region in ("summary", "device", "diagnostics"):
        assert f'data-live="{region}"' in page
    assert "refreshLive" in admin.get("/static/js/app.js").get_data(as_text=True)
