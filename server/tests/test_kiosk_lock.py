"""Lock player, auto-resume, remote unlock/relock and the shell lock diagnostics."""
import json
import re
from datetime import timedelta

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from aisignx.displays import options
from aisignx.extensions import db
from aisignx.models import AuditLog, Display, PairingCode
from aisignx.player import diag
from aisignx.player.broker import get_broker
from aisignx.timeutil import utcnow

from .conftest import bearer, login, make_user
from .test_player_api import API, content  # noqa: F401  (fixture)
from .test_player_stream import _drain

RID = re.compile(r"[0-9a-f]{16}")


def _form(**overrides) -> dict:
    data = {"name": "Lobby", "playlist": 0, "group": 0, "orientation": 0, "fit_override": "",
            "volume": 100, "brightness": 100, "show_offline_banner": "y", "resume_after_min": 5}
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def _display(app, display_id) -> Display:
    with app.app_context():
        display = db.session.get(Display, display_id)
        db.session.expunge(display)
        return display


def _set(app, display_id, **values) -> None:
    with app.app_context():
        display = db.session.get(Display, display_id)
        for key, value in values.items():
            setattr(display, key, value)
        db.session.commit()


def _subscribe(app, display_id):
    with app.app_context():
        return get_broker().subscribe(display_id)


def _actions(app, action: str) -> list:
    with app.app_context():
        return [json.loads(r.detail) if isinstance(r.detail, str) else r.detail
                for r in AuditLog.query.filter_by(action=action).order_by(AuditLog.id)]


# --- migration --------------------------------------------------------------------------

def test_migration_keeps_pairing_codes_and_play_events(app):
    with app.app_context():
        downgrade(revision="e5a9c3d17b42")
        cols = {c["name"] for c in inspect(db.engine).get_columns("displays")}
        assert "kiosk_lock" not in cols and "resume_after_s" not in cols
        now = utcnow().replace(tzinfo=None)
        stamp = now.isoformat(sep=" ")
        with db.engine.begin() as conn:
            conn.execute(db.text("INSERT INTO displays (name, created_at) VALUES ('Old', :now)"),
                         {"now": stamp})
            display_id = conn.execute(db.text("SELECT id FROM displays")).scalar()
            conn.execute(db.text(
                "INSERT INTO pairing_codes (code, secret_hash, created_at, expires_at, display_id)"
                " VALUES ('K7PQ4X', :h, :now, :exp, :d)"),
                {"h": "a" * 64, "now": stamp,
                 "exp": (now + timedelta(minutes=2)).isoformat(sep=" "), "d": display_id})
            conn.execute(db.text(
                "INSERT INTO play_events (display_id, client_event_id, media_id, started_at,"
                " duration_ms, received_at) VALUES (:d, 'e1', 1, :now, 5000, :now)"),
                {"d": display_id, "now": stamp})
        upgrade()
        db.session.remove()

        def counts():
            with db.engine.connect() as conn:
                return (PairingCode.query.count(),
                        conn.execute(db.text("SELECT COUNT(*) FROM play_events")).scalar())

        assert counts() == (1, 1)
        display = db.session.get(Display, display_id)
        assert display.kiosk_lock is False and display.resume_after_s == 300
        db.session.remove()
        downgrade(revision="e5a9c3d17b42")
        assert counts() == (1, 1)
        upgrade()
        assert counts() == (1, 1)


# --- edit form --------------------------------------------------------------------------

def test_lock_needs_a_pin(app, admin_client, paired_display):
    display_id = paired_display[0]
    resp = admin_client.post(f"/displays/{display_id}/edit", data=_form(kiosk_lock="y"))
    assert resp.status_code == 200
    assert "Set an unlock PIN first" in resp.get_data(as_text=True)
    assert _display(app, display_id).kiosk_lock is False

    # A new PIN in the same submit is enough.
    resp = admin_client.post(f"/displays/{display_id}/edit",
                             data=_form(kiosk_lock="y", unlock_pin="2468",
                                        resume_after_min=10))
    assert resp.status_code == 302
    display = _display(app, display_id)
    assert display.kiosk_lock is True and display.resume_after_s == 600
    assert options.verify_pin(display.unlock_pin_hash, "2468")

    # The stored PIN keeps it valid; the edit page shows the saved values.
    resp = admin_client.post(f"/displays/{display_id}/edit", data=_form(kiosk_lock="y"))
    assert resp.status_code == 302
    page = admin_client.get(f"/displays/{display_id}/edit").get_data(as_text=True)
    assert re.search(r'name="kiosk_lock"[^>]*checked|checked[^>]*name="kiosk_lock"', page)
    assert 'value="5"' in page


def test_clear_pin_while_locked_is_rejected(app, admin_client, paired_display):
    display_id = paired_display[0]
    admin_client.post(f"/displays/{display_id}/edit",
                      data=_form(kiosk_lock="y", unlock_pin="2468"))
    stored = _display(app, display_id).unlock_pin_hash
    resp = admin_client.post(f"/displays/{display_id}/edit",
                             data=_form(kiosk_lock="y", clear_pin="y"))
    assert resp.status_code == 200
    assert "Turn off Lock player before removing the PIN." in resp.get_data(as_text=True)
    assert _display(app, display_id).unlock_pin_hash == stored

    # Turning the lock off in the same submit is fine.
    resp = admin_client.post(f"/displays/{display_id}/edit", data=_form(clear_pin="y"))
    assert resp.status_code == 302
    display = _display(app, display_id)
    assert display.kiosk_lock is False and display.unlock_pin_hash is None


@pytest.mark.parametrize("value", [0, 61, "x"])
def test_resume_minutes_validated(app, admin_client, paired_display, value):
    display_id = paired_display[0]
    resp = admin_client.post(f"/displays/{display_id}/edit",
                             data=_form(resume_after_min=value))
    assert resp.status_code == 200 and "is-invalid" in resp.get_data(as_text=True)
    assert _display(app, display_id).resume_after_s == 300


def test_lock_change_is_audited(app, admin_client, paired_display):
    display_id = paired_display[0]
    admin_client.post(f"/displays/{display_id}/edit",
                      data=_form(kiosk_lock="y", unlock_pin="2468", resume_after_min=2))
    changed = _actions(app, "display.update")[-1]["changed"]
    assert {"kiosk_lock", "resume_after_s", "unlock_pin"} <= set(changed)


# --- manifest ---------------------------------------------------------------------------

def test_manifest_version_changes_with_lock(app, client, content):  # noqa: F811
    def fetch():
        return client.get(f"{API}/manifest", headers=bearer(content["token"])).get_json()

    before = fetch()
    assert before["settings"]["kiosk_lock"] is False
    assert before["settings"]["resume_after_s"] == 300
    _set(app, content["display_id"], kiosk_lock=True, resume_after_s=900)
    after = fetch()
    assert after["settings"]["kiosk_lock"] is True
    assert after["settings"]["resume_after_s"] == 900
    assert after["version"] != before["version"]


# --- remote unlock / relock -------------------------------------------------------------

def test_unlock_and_relock_send_commands(app, admin_client, paired_display):
    display_id = paired_display[0]
    sub = _subscribe(app, display_id)
    resp = admin_client.post(f"/displays/{display_id}/unlock", data={"minutes": 30})
    assert resp.status_code == 302
    resp = admin_client.post(f"/displays/{display_id}/relock", follow_redirects=True)
    assert b"Locked again." in resp.data
    events = _drain(sub)
    assert [e for e, _ in events] == ["command", "command"]
    unlock, lock = events[0][1], events[1][1]
    assert set(unlock) == {"action", "minutes", "request_id"}
    assert (unlock["action"], unlock["minutes"]) == ("unlock", 30)
    assert set(lock) == {"action", "request_id"} and lock["action"] == "lock"
    assert RID.fullmatch(unlock["request_id"]) and RID.fullmatch(lock["request_id"])
    assert unlock["request_id"] != lock["request_id"]
    assert _actions(app, "display.unlock") == [{"minutes": 30, "delivered": True}]
    assert _actions(app, "display.relock") == [{"delivered": True}]


def test_unlock_flash(app, admin_client, paired_display):
    display_id = paired_display[0]
    sub = _subscribe(app, display_id)
    resp = admin_client.post(f"/displays/{display_id}/unlock", data={"minutes": 15},
                             follow_redirects=True)
    assert b"Unlocked for 15 minutes." in resp.data
    _drain(sub)


def test_unlock_not_connected(app, admin_client, paired_display):
    display_id = paired_display[0]
    resp = admin_client.post(f"/displays/{display_id}/unlock", data={"minutes": 5},
                             follow_redirects=True)
    assert b"The display is not connected right now." in resp.data
    assert _actions(app, "display.unlock") == [{"minutes": 5, "delivered": False}]


def test_unlock_not_paired(app, admin_client):
    with app.app_context():
        display = Display(name="Spare")
        db.session.add(display)
        db.session.commit()
        display_id = display.id
    resp = admin_client.post(f"/displays/{display_id}/unlock", data={"minutes": 5},
                             follow_redirects=True)
    assert b"This display is not paired." in resp.data


@pytest.mark.parametrize("minutes", [7, 0, 240, "x", None])
def test_unlock_bad_minutes(admin_client, paired_display, minutes):
    data = {} if minutes is None else {"minutes": minutes}
    resp = admin_client.post(f"/displays/{paired_display[0]}/unlock", data=data)
    assert resp.status_code == 400


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_unlock_admin_only(app, configured, client, paired_display, role):
    make_user(app, role, role)
    login(client, role)
    display_id = paired_display[0]
    assert client.post(f"/displays/{display_id}/unlock",
                       data={"minutes": 15}).status_code == 403
    assert client.post(f"/displays/{display_id}/relock").status_code == 403


def test_unlock_needs_login(configured, client, paired_display):
    resp = client.post(f"/displays/{paired_display[0]}/unlock", data={"minutes": 15})
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


# --- detail page ------------------------------------------------------------------------

def test_detail_unlock_form_only_for_admin_and_locked(app, configured, client,
                                                      paired_display):
    display_id = paired_display[0]
    url = f"/displays/{display_id}/unlock"
    login(client, "admin")
    page = client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert url not in page and "Lock player" in page

    _set(app, display_id, kiosk_lock=True, unlock_pin_hash=options.hash_pin("2468"))
    page = client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert url in page and f"/displays/{display_id}/relock" in page
    assert "Unlock remotely" in page and "Locked" in page

    make_user(app, "editor", "editor")
    editor = app.test_client()
    login(editor, "editor")
    page = editor.get(f"/displays/{display_id}").get_data(as_text=True)
    assert url not in page and "Locked" in page

    _set(app, display_id, token_hash=None)
    page = client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert url not in page


# --- diagnostics ------------------------------------------------------------------------

def test_diag_keeps_shell_lock_truncated():
    text = diag.clean({"shell": {"name": "electron", "lock": "unlocked-and-much-longer"}})
    assert json.loads(text)["shell"] == {"name": "electron", "lock": "unlocked-and-muc"}
    text = diag.clean({"shell": {"name": "electron", "lock": 3}})
    assert "lock" not in json.loads(text)["shell"]


def test_device_card_shows_shell_lock(app, admin_client, paired_display):
    display_id = paired_display[0]
    _set(app, display_id, diagnostics=json.dumps(
        {"shell": {"name": "android", "version": "3.0", "lock": "pinned"}}),
        diagnostics_at=utcnow())
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert "lock: pinned" in page
