"""Device agent: settings, capability reporting, the durable command queue (docs/device-agent-design.md)."""
import json
from datetime import timedelta

import pytest
from sqlalchemy import update

from aisignx.displays import agent
from aisignx.extensions import db
from aisignx.models import DeviceCommand, Display
from aisignx.player import diag
from aisignx.timeutil import utcnow

from .conftest import DISPLAY_TOKEN, bearer, login, make_user

API = "/api/player/v1"
CAPS = {"agent": {"version": "3.1.0", "tier": "plain", "can_restart": True, "can_collect_logs": True,
                  "watchdog": True}}


def _setup(app, paired_display, restart=True, logs=True, caps=CAPS):
    with app.app_context():
        d = db.session.get(Display, paired_display[0])
        d.agent_allow_restart, d.agent_allow_logs = restart, logs
        d.diagnostics = json.dumps(caps) if caps is not None else None
        db.session.commit()
    return paired_display[0]


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _post(admin, did, action):
    return admin.post(f"/displays/{did}/agent/{action}", data={})


# --- capability reporting ------------------------------------------------------------------

def test_diag_keeps_only_valid_agent_facts():
    out = json.loads(diag.clean({"agent": {"version": "3.1.0" + "x" * 40, "tier": "root", "can_restart": "yes",
        "can_collect_logs": True, "restarts_1h": 3.7, "gave_up": False, "token": "no"}}))["agent"]
    assert out["can_collect_logs"] is True and out["restarts_1h"] == 3 and out["gave_up"] is False
    assert "tier" not in out and "can_restart" not in out and "token" not in out and len(out["version"]) == 20
    assert json.loads(diag.clean({"agent": {"tier": "device_owner"}}))["agent"]["tier"] == "device_owner"


def test_agent_settings_reach_the_manifest_and_only_admins_change_them(app, configured, client, paired_display, admin):
    did = paired_display[0]
    r = admin.post(f"/displays/{did}/edit", data={"name": "Lobby", "playlist": "0", "group": "0", "orientation": "0",
        "fit_override": "", "volume": "100", "brightness": "100", "show_offline_banner": "y", "resume_after_min": "5",
        "agent_watchdog": "y", "agent_allow_restart": "y"})
    assert r.status_code in (302, 303)
    m = client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).get_json()
    assert m["settings"]["agent"] == {"watchdog": True, "restart": True, "logs": False, "reboot": False,
                                         "startup": False, "update": False}
    make_user(app, "ed", "editor")
    ed = app.test_client()
    login(ed, "ed")
    ed.post(f"/displays/{did}/edit", data={"name": "Lobby", "playlist": "0", "group": "0", "orientation": "0",
        "fit_override": "", "volume": "100", "brightness": "100", "agent_allow_logs": "y", "agent_watchdog": ""})
    with app.app_context():
        d = db.session.get(Display, did)
        assert (d.agent_watchdog, d.agent_allow_restart, d.agent_allow_logs) == (True, True, False)  # unchanged


# --- creating commands --------------------------------------------------------------------

def test_a_command_needs_the_switch_the_device_ability_and_an_admin(app, paired_display, admin):
    did = _setup(app, paired_display, restart=False)
    _post(admin, did, "restart_player")
    with app.app_context():
        assert DeviceCommand.query.count() == 0          # switched off for this display
    _setup(app, paired_display, caps={})
    _post(admin, did, "restart_player")
    with app.app_context():
        assert DeviceCommand.query.count() == 0          # the app has not reported an agent
    _setup(app, paired_display, caps={"agent": {"can_restart": False}})
    _post(admin, did, "restart_player")
    with app.app_context():
        assert DeviceCommand.query.count() == 0          # the app cannot do it
    _setup(app, paired_display)
    _post(admin, did, "format_disk")                      # not on the closed list
    assert _post(admin, did, "restart_player").status_code in (302, 303)
    with app.app_context():
        c = DeviceCommand.query.one()
        assert c.action == "restart_player" and c.state == "pending" and len(c.request_id) == 16
    make_user(app, "ed", "editor")
    ed = app.test_client()
    login(ed, "ed")
    assert _post(ed, did, "collect_logs").status_code in (302, 403)
    with app.app_context():
        assert DeviceCommand.query.count() == 1          # an editor cannot issue one


def test_one_open_command_per_action_and_an_hourly_cap(app, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "restart_player")
    _post(admin, did, "restart_player")
    with app.app_context():
        assert DeviceCommand.query.count() == 1
        for i in range(9):  # fill the hour with finished commands
            db.session.add(DeviceCommand(display_id=did, request_id=f"{i:016x}", action="collect_logs",
                                         state="done", created_at=utcnow(), expires_at=utcnow()))
        db.session.commit()
    _post(admin, did, "collect_logs")
    with app.app_context():
        assert DeviceCommand.query.count() == 10         # the 11th in an hour is refused


# --- the display fetches and acknowledges its own commands ---------------------------------

def test_delivery_runs_a_command_at_most_once_and_ack_is_scoped(app, configured, client, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "restart_player")
    hb = client.post(f"{API}/heartbeat", headers=bearer(DISPLAY_TOKEN), json={"client_id": "abc"}).get_json()
    assert hb["commands"] == 1
    got = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]
    assert len(got) == 1 and got[0]["action"] == "restart_player" and 0 < got[0]["expires_in_s"] <= 120
    assert client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"] == []  # not twice
    rid = got[0]["id"]
    assert client.post(f"{API}/commands/{rid}/ack", headers=bearer(DISPLAY_TOKEN), json={"state": "bogus"}).status_code == 400
    ok = client.post(f"{API}/commands/{rid}/ack", headers=bearer(DISPLAY_TOKEN), json={"state": "done", "result": "restarted"})
    assert ok.status_code == 200 and ok.get_json()["state"] == "done"
    assert client.post(f"{API}/commands/{rid}/ack", headers=bearer(DISPLAY_TOKEN), json={"state": "done"}).status_code == 200  # idempotent
    assert client.post(f"{API}/commands/{rid}/ack", headers=bearer(DISPLAY_TOKEN), json={"state": "failed"}).status_code == 409
    # Another display cannot see or acknowledge this one's commands.
    with app.app_context():
        other = Display(name="Other", token_hash=__import__("aisignx.player.auth", fromlist=["x"]).hash_token("other-token-0123456789abcdefghijklmnopqrstuvw"),
                        paired_at=utcnow())
        db.session.add(other)
        db.session.commit()
    h = bearer("other-token-0123456789abcdefghijklmnopqrstuvw")
    assert client.get(f"{API}/commands", headers=h).get_json()["commands"] == []
    assert client.post(f"{API}/commands/{rid}/ack", headers=h, json={"state": "done"}).status_code == 404


def test_expired_commands_are_not_delivered_or_acknowledged(app, configured, client, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "restart_player")
    with app.app_context():
        c = DeviceCommand.query.one()
        c.expires_at = utcnow() - timedelta(seconds=1)
        rid = c.request_id
        db.session.commit()
    assert client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"] == []
    with app.app_context():
        assert DeviceCommand.query.one().state == "expired"
    assert client.post(f"{API}/commands/{rid}/ack", headers=bearer(DISPLAY_TOKEN), json={"state": "done"}).status_code == 409


def test_job_expires_and_cleans_up(app, paired_display):
    did = _setup(app, paired_display)
    with app.app_context():
        old = utcnow() - timedelta(days=40)
        db.session.add_all([
            DeviceCommand(display_id=did, request_id="a" * 16, action="restart_player", state="pending",
                          created_at=utcnow() - timedelta(minutes=5), expires_at=utcnow() - timedelta(minutes=3)),
            DeviceCommand(display_id=did, request_id="b" * 16, action="restart_player", state="done",
                          created_at=old, expires_at=old)])
        db.session.commit()
        assert "expired 1" in agent.job() and DeviceCommand.query.count() == 1
        assert DeviceCommand.query.one().state == "expired"


def test_display_page_shows_the_agent(app, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "restart_player")
    page = admin.get(f"/displays/{did}").get_data(as_text=True)
    assert "Device agent" in page and "App agent 3.1.0" in page and "Restart the player" in page
    assert "pending" in page and 'disabled' not in page.split("Restart the player")[0][-200:]


# --- logs ----------------------------------------------------------------------------------

import gzip


def _log_command(app, admin, client, paired_display):
    did = _setup(app, paired_display)
    _post(admin, did, "collect_logs")
    got = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]
    return did, got[0]["id"]


def test_a_log_is_accepted_only_for_an_open_delivered_collect_logs_command(app, configured, client, paired_display, admin):
    did, rid = _log_command(app, admin, client, paired_display)
    log = gzip.compress(b"2026-11-15 player started\n" * 50)
    h = bearer(DISPLAY_TOKEN)
    assert client.post(f"{API}/commands/{rid}/logs", headers=h, data=b"plain text, not gzip").status_code == 400
    assert client.post(f"{API}/commands/{'0' * 16}/logs", headers=h, data=log).status_code == 404
    assert client.post(f"{API}/commands/{rid}/logs", headers=h, data=b"").status_code == 400
    assert client.post(f"{API}/commands/{rid}/logs", headers=h, data=b"\x1f\x8b" + b"x" * (agent.MAX_LOG_BYTES + 1)).status_code == 413
    assert client.post(f"{API}/commands/{rid}/logs", headers=h, data=log).status_code == 200
    assert client.post(f"{API}/commands/{rid}/logs", headers=h, data=log).status_code == 409   # once
    with app.app_context():
        c = DeviceCommand.query.one()
        assert c.state == "done" and "uploaded" in c.result
    # A restart command cannot be used to upload a file.
    _post(admin, did, "restart_player")
    with app.app_context():
        r2 = DeviceCommand.query.filter_by(action="restart_player").one().request_id
    client.get(f"{API}/commands", headers=h)
    assert client.post(f"{API}/commands/{r2}/logs", headers=h, data=log).status_code == 404


def test_only_admins_download_a_log_and_the_path_is_the_servers(app, configured, client, paired_display, admin):
    did, rid = _log_command(app, admin, client, paired_display)
    log = gzip.compress(b"hello log")
    client.post(f"{API}/commands/{rid}/logs", headers=bearer(DISPLAY_TOKEN), data=log)
    r = admin.get(f"/displays/{did}/logs/{rid}")
    assert r.status_code == 200 and r.data == log and "attachment" in r.headers["Content-Disposition"]
    assert "Download" in admin.get(f"/displays/{did}").get_data(as_text=True)
    assert admin.get(f"/displays/{did}/logs/..%2f..%2fsecret").status_code == 404
    assert admin.get(f"/displays/{did}/logs/{'f' * 16}").status_code == 404
    make_user(app, "ed", "editor")
    ed = app.test_client()
    login(ed, "ed")
    assert ed.get(f"/displays/{did}/logs/{rid}").status_code in (302, 403)


def test_old_logs_are_pruned(app, paired_display):
    import os
    did = _setup(app, paired_display)
    with app.app_context():
        for i in range(8):
            agent.log_path(did, f"{i:016x}").write_bytes(b"\x1f\x8b x")
        agent._prune(did)
        assert len(list(agent.logs_dir(did).glob("*.log.gz"))) == agent.LOGS_PER_DISPLAY
        old = agent.log_path(did, "a" * 16)
        old.write_bytes(b"\x1f\x8b x")
        os.utime(old, (1, 1))
        assert agent.prune_all() >= 1 and not old.exists()


def test_log_upload_accepts_the_content_type_the_apps_send(app, configured, client, paired_display, admin):
    did, rid = _log_command(app, admin, client, paired_display)
    r = client.post(f"{API}/commands/{rid}/logs", headers=bearer(DISPLAY_TOKEN), data=gzip.compress(b"x" * 100),
                    content_type="application/gzip")
    assert r.status_code == 200
