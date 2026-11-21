"""The privileged Windows agent: enrollment, its own token, scoping, the encrypted auto-login command."""
import base64
import json
import logging
import re
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from sqlalchemy import select, text

from aisignx.displays import ecies, enroll
from aisignx.extensions import db
from aisignx.models import AuditLog, DeviceCommand, Display
from aisignx.player import agent_api
from aisignx.timeutil import utcnow

from .conftest import DISPLAY_TOKEN, PASSWORD, bearer, login, make_user

API = "/api/player/v1"
SHA = "ab" * 32
CODE_RE = re.compile(r"[A-Z2-9]{4}(?:-[A-Z2-9]{4}){3}")
WIN_PASSWORD = "Sup3r secret ü!"
CAPS = {"agent": {"version": "3.1.0", "tier": "plain", "can_restart": True, "can_collect_logs": True,
                  "can_reboot": True, "can_set_startup": True, "can_update": True}, "shell": {"name": "electron"}}
AGENT_DIAG = {"version": "1.0.0", "state": "running", "host": "KIOSK1", "kiosk_user": "kiosk", "can_reboot": True,
              "can_update": True, "can_set_startup": True, "can_autologin": True,
              "startup": {"autostart": False, "mode": "app", "shell_capable": True}}


@pytest.fixture(autouse=True)
def _fresh_rate_limit():
    agent_api.reset_rate_limit()


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


@pytest.fixture
def display(app, paired_display):
    did = paired_display[0]
    with app.app_context():
        d = db.session.get(Display, did)
        d.diagnostics = json.dumps(CAPS)
        d.agent_allow_reboot = d.agent_allow_startup = d.agent_allow_update = True
        db.session.commit()
    return did


def _code(admin, did):
    r = admin.post(f"/displays/{did}/agent-enroll/code")
    assert r.status_code == 200
    return CODE_RE.search(r.get_data(as_text=True)).group(0)


def _keys():
    priv = X25519PrivateKey.generate()
    pub = base64.b64encode(priv.public_key().public_bytes_raw()).decode()
    return priv, pub


def _enroll(client, code, pub):
    return client.post(f"{API}/agent/enroll", json={"code": code, "public_key": pub, "host": "KIOSK1", "version": "1.0.0"})


@pytest.fixture
def enrolled(app, admin, display, client):
    """(display id, agent token, agent X25519 private key) with the agent already reporting its diagnostics."""
    priv, pub = _keys()
    r = _enroll(client, _code(admin, display), pub)
    assert r.status_code == 200, r.get_data(as_text=True)
    token = r.get_json()["agent_token"]
    assert client.post(f"{API}/agent/diagnostics", json={"diag": AGENT_DIAG}, headers=bearer(token)).status_code == 200
    return display, token, priv


def _agent_commands(client, token):
    r = client.get(f"{API}/agent/commands", headers=bearer(token))
    assert r.status_code == 200
    return r.get_json()


# --- enrollment -----------------------------------------------------------------------------------

def test_enrollment_code_needs_admin_and_is_stored_only_as_a_hash(app, configured, display):
    make_user(app, "ed", "editor")
    c = app.test_client()
    login(c, "ed")
    assert c.post(f"/displays/{display}/agent-enroll/code").status_code == 403
    make_user(app, "boss2", "admin")
    a = app.test_client()
    login(a, "boss2")
    code = _code(a, display)
    with app.app_context():
        d = db.session.get(Display, display)
        assert d.agent_enroll_hash and code.replace("-", "") not in d.agent_enroll_hash
        assert d.agent_enroll_hash != code and len(d.agent_enroll_hash) == 64
        assert audit_actions(app).count("display.agent_enroll_code") == 1


def audit_actions(app):
    with app.app_context():
        return [r.action for r in AuditLog.query.all()]


def test_enroll_gives_a_scoped_token_once_and_burns_the_code(app, admin, display, client):
    code = _code(admin, display)
    _priv, pub = _keys()
    r = _enroll(client, code.lower().replace("-", " "), pub)  # forgiving about case and separators
    body = r.get_json()
    assert r.status_code == 200 and body["agent_token"].startswith("agt_") and len(body["agent_token"]) == 47
    assert body["display_id"] == display and len(base64.b64decode(body["command_key"])) == 32
    assert len(body["fingerprint"].split()) == 4
    assert _enroll(client, code, pub).status_code == 403           # single use
    with app.app_context():
        d = db.session.get(Display, display)
        assert d.agent_token_hash and body["agent_token"] not in d.agent_token_hash
        assert d.agent_enroll_hash is None and d.agent_pubkey == pub
        assert "display.agent_enrolled" in audit_actions(app)
        assert body["agent_token"] not in json.dumps([r.detail for r in AuditLog.query.all()], default=str)


def test_expired_and_wrong_codes_are_refused_and_do_not_kill_the_live_code(app, admin, display, client):
    _priv, pub = _keys()
    code = _code(admin, display)
    with app.app_context():
        db.session.execute(text("UPDATE displays SET agent_enroll_expires = :t WHERE id = :i"),
                           {"t": utcnow() - timedelta(seconds=1), "i": display})
        db.session.commit()
    assert _enroll(client, code, pub).status_code == 403
    code = _code(admin, display)
    for _ in range(enroll.MAX_FAILS):
        assert _enroll(client, "ABCD-EFGH-JKLM-NPQR", pub).status_code == 403
    assert _enroll(client, code, pub).status_code == 200            # wrong guesses do not strike a real code
    assert _enroll(client, "not a code", pub).status_code == 403
    assert _enroll(client, _code(admin, display), "bad-key").status_code == 400
    assert _enroll(client, _code(admin, display), base64.b64encode(b"x" * 31).decode()).status_code == 400


def test_enroll_is_rate_limited_per_address(app, configured, client):
    _priv, pub = _keys()
    codes = [_enroll(client, "ABCD-EFGH-JKLM-NPQR", pub).status_code for _ in range(agent_api.ENROLL_PER_MINUTE + 2)]
    assert codes[-1] == 429 and set(codes[:-2]) == {403}


def test_enrolling_again_replaces_the_token(app, admin, display, client):
    _p1, pub1 = _keys()
    first = _enroll(client, _code(admin, display), pub1).get_json()["agent_token"]
    _p2, pub2 = _keys()
    second = _enroll(client, _code(admin, display), pub2).get_json()["agent_token"]
    assert client.get(f"{API}/agent/command-key", headers=bearer(first)).status_code == 401
    assert client.get(f"{API}/agent/command-key", headers=bearer(second)).status_code == 200
    with app.app_context():
        assert db.session.get(Display, display).agent_pubkey == pub2


def test_a_code_cannot_be_made_for_an_unpaired_display_and_enrolling_one_is_refused(app, admin):
    with app.app_context():
        d = Display(name="Raw")
        db.session.add(d)
        db.session.commit()
        did = d.id
    assert admin.post(f"/displays/{did}/agent-enroll/code").status_code == 400


# --- token scoping --------------------------------------------------------------------------------

AGENT_GETS = ("/agent/commands", "/agent/command-key", "/agent/status")


def test_display_token_never_works_on_agent_routes_and_vice_versa(app, enrolled, client):
    did, token, _ = enrolled
    for path in AGENT_GETS:
        assert client.get(API + path, headers=bearer(DISPLAY_TOKEN)).status_code == 401
        assert client.get(API + path).status_code == 401
        assert client.get(API + path, headers=bearer(token)).status_code == 200
    assert client.post(f"{API}/agent/diagnostics", json={}, headers=bearer(DISPLAY_TOKEN)).status_code == 401
    assert client.get(f"{API}/manifest", headers=bearer(token)).status_code == 401
    assert client.post(f"{API}/heartbeat", json={}, headers=bearer(token)).status_code == 401
    assert client.get(f"{API}/commands", headers=bearer(token)).status_code == 401
    # the agent token is not accepted from a cookie or a query string either
    client.set_cookie("aisignx_display", token, path="/api/player")
    assert client.get(f"{API}/agent/status").status_code == 401
    assert client.get(f"{API}/agent/status?token={token}").status_code == 401


def test_garbage_tokens_are_rejected(client, enrolled):
    for bad in ("agt_" + "x" * 43, "agt_short", "x" * 500, "", "Bearer"):
        assert client.get(f"{API}/agent/status", headers={"Authorization": "Bearer " + bad}).status_code == 401


def test_status_reports_the_player_for_the_health_check(app, enrolled, client):
    did, token, _ = enrolled
    client.post(f"{API}/heartbeat", json={"player_version": "3.2.0"}, headers=bearer(DISPLAY_TOKEN))
    s = client.get(f"{API}/agent/status", headers=bearer(token)).get_json()
    assert s["player_version"] == "3.2.0" and 0 <= s["player_last_seen_s"] < 30


# --- exclusivity ----------------------------------------------------------------------------------

def _ask(admin, did, action, **form):
    return admin.post(f"/displays/{did}/agent/{action}", data=form)


def test_tier_b_goes_only_to_the_agent_once_it_is_enrolled(app, admin, enrolled, client):
    did, token, _ = enrolled
    _ask(admin, did, "reboot_device")
    # not to the player: nothing in /commands, no count in the heartbeat, no key, no ack
    assert client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"] == []
    hb = client.post(f"{API}/heartbeat", json={}, headers=bearer(DISPLAY_TOKEN)).get_json()
    assert hb["commands"] == 0
    assert client.get(f"{API}/command-key", headers=bearer(DISPLAY_TOKEN)).status_code == 409
    got = _agent_commands(client, token)["commands"]
    assert [c["action"] for c in got] == ["reboot_device"] and "signed" in got[0]
    assert client.post(f"{API}/commands/{got[0]['id']}/ack", json={"state": "done"},
                       headers=bearer(DISPLAY_TOKEN)).status_code == 404
    r = client.post(f"{API}/agent/commands/{got[0]['id']}/ack", json={"state": "done", "result": "rebooting"},
                    headers=bearer(token))
    assert r.status_code == 200 and r.get_json()["state"] == "done"


def test_tier_b_returns_to_the_player_after_the_agent_is_removed(app, admin, enrolled, client):
    did, token, _ = enrolled
    assert admin.post(f"/displays/{did}/agent-enroll/remove").status_code in (200, 302)
    assert client.get(f"{API}/agent/status", headers=bearer(token)).status_code == 401
    _ask(admin, did, "reboot_device")
    got = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]
    assert [c["action"] for c in got] == ["reboot_device"]
    assert client.get(f"{API}/command-key", headers=bearer(DISPLAY_TOKEN)).status_code == 200
    assert "display.agent_removed" in audit_actions(app)


def test_the_agent_gets_no_tier_a_commands_and_the_player_still_does(app, admin, enrolled, client):
    did, token, _ = enrolled
    with app.app_context():
        db.session.execute(text("UPDATE displays SET agent_allow_restart = 1 WHERE id = :i"), {"i": did})
        db.session.commit()
    _ask(admin, did, "restart_player")
    assert _agent_commands(client, token)["commands"] == []
    assert [c["action"] for c in client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]] == ["restart_player"]


def test_privileged_abilities_come_from_the_agent_not_the_player(app, admin, display, client):
    # The player claims everything, but there is an agent that has not reported anything yet.
    _p, pub = _keys()
    token = _enroll(client, _code(admin, display), pub).get_json()["agent_token"]
    r = _ask(admin, display, "reboot_device")
    with app.app_context():
        assert DeviceCommand.query.count() == 0
    client.post(f"{API}/agent/diagnostics", json={"diag": {"can_reboot": True}}, headers=bearer(token))
    _ask(admin, display, "reboot_device")
    with app.app_context():
        assert DeviceCommand.query.count() == 1


def test_a_stale_agent_cannot_be_given_commands(app, admin, enrolled):
    did, token, _ = enrolled
    with app.app_context():
        db.session.execute(text("UPDATE displays SET agent_seen_at = :t WHERE id = :i"),
                           {"t": utcnow() - timedelta(hours=1), "i": did})
        db.session.commit()
    _ask(admin, did, "reboot_device")
    with app.app_context():
        assert DeviceCommand.query.count() == 0


def test_update_package_only_for_the_agents_own_open_update_command(app, admin, enrolled, client):
    from aisignx.models import Download
    did, token, _ = enrolled
    assert client.get(f"{API}/agent/commands/0123456789abcdef/package", headers=bearer(token)).status_code == 404
    assert client.get(f"{API}/commands/0123456789abcdef/package", headers=bearer(DISPLAY_TOKEN)).status_code == 409


# --- diagnostics ----------------------------------------------------------------------------------

def test_agent_diagnostics_are_whitelisted_and_the_public_key_cannot_be_replaced(app, enrolled, client):
    did, token, _ = enrolled
    evil = dict(AGENT_DIAG, public_key="AAAA", extra={"x": 1}, host="h" * 500, state="hacked", can_reboot="yes",
                key_id="nothex", autologin={"enabled": True, "user": "kiosk", "password": "oops"})
    r = client.post(f"{API}/agent/diagnostics", json={"diag": evil}, headers=bearer(token))
    assert r.status_code == 200
    with app.app_context():
        d = db.session.get(Display, did)
        stored = json.loads(d.agent_diag)
        assert "public_key" not in stored and "extra" not in stored and "password" not in json.dumps(stored)
        assert len(stored["host"]) == 64 and "state" not in stored and "can_reboot" not in stored
        assert "key_id" not in stored and d.agent_seen_at is not None
    assert client.post(f"{API}/agent/diagnostics", data="[1]", content_type="application/json",
                       headers=bearer(token)).status_code == 400


def test_player_diag_whitelist_knows_the_new_flags():
    from aisignx.player import diag
    out = json.loads(diag.clean({"agent": {"can_autologin": True, "privileged_agent": True, "evil": 1}}))
    assert "agent" not in out   # the player cannot claim privileged-agent facts


# --- auto-login -----------------------------------------------------------------------------------

def _autologin(admin, did, **over):
    data = {"user": "kiosk", "password": WIN_PASSWORD, "confirm": PASSWORD, "do": "set"}
    data.update(over)
    return admin.post(f"/displays/{did}/autologin", data=data)


def test_autologin_envelope_round_trip_and_wipe_after_delivery(app, admin, enrolled, client, caplog):
    did, token, priv = enrolled
    caplog.set_level(logging.DEBUG)
    r = _autologin(admin, did)
    assert r.status_code in (200, 302) and WIN_PASSWORD not in r.get_data(as_text=True)
    with app.app_context():
        cmd = DeviceCommand.query.filter_by(action="set_autologin").one()
        stored = dict(cmd.params)
        rid = cmd.request_id
    assert stored["enabled"] is True and stored["user"] == "kiosk" and stored["domain"] == "."
    assert WIN_PASSWORD not in json.dumps(stored) and stored["key_id"] == ecies.key_id(priv.public_key().public_bytes_raw())
    assert ecies.decrypt(priv, stored["enc"], ecies.aad(did, rid, "kiosk", ".")) == WIN_PASSWORD.encode("utf-8")
    # the player never sees it
    assert client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"] == []
    # the agent does, signed, and the envelope in the signed payload decrypts
    got = _agent_commands(client, token)["commands"]
    payload = json.loads(got[0]["signed"]["payload"])
    assert payload["action"] == "set_autologin" and payload["params"]["enc"] == stored["enc"]
    assert ecies.decrypt(priv, payload["params"]["enc"], ecies.aad(did, rid, "kiosk", ".")) == WIN_PASSWORD.encode()
    # ... and the database no longer holds the ciphertext
    with app.app_context():
        after = DeviceCommand.query.filter_by(action="set_autologin").one()
        assert after.params == {"enabled": True, "user": "kiosk", "delivered": True}
        raw = " ".join(str(v) for row in db.session.execute(text("SELECT * FROM device_commands")) for v in row)
        assert stored["enc"] not in raw
        # not in the audit log either; the password never anywhere
        everything = json.dumps([(a.action, a.detail) for a in AuditLog.query.all()], default=str)
        assert WIN_PASSWORD not in everything and stored["enc"] not in everything
    assert WIN_PASSWORD not in caplog.text and stored["enc"] not in caplog.text
    # delivered once: asking again returns nothing
    assert _agent_commands(client, token)["commands"] == []
    # wrong AAD (another display, request, user) does not decrypt
    for bad in (ecies.aad(did + 1, rid, "kiosk", "."), ecies.aad(did, "0" * 16, "kiosk", "."),
                ecies.aad(did, rid, "other", ".")):
        assert ecies.decrypt(priv, stored["enc"], bad) is None


def test_autologin_is_admin_only_needs_password_account_and_a_private_channel(app, admin, enrolled):
    did, token, _ = enrolled
    make_user(app, "ed2", "editor")
    e = app.test_client()
    login(e, "ed2")
    assert _autologin(e, did).status_code == 403
    def count():
        with app.app_context():
            return DeviceCommand.query.filter_by(action="set_autologin").count()
    _autologin(admin, did, confirm="wrong")
    _autologin(admin, did, user="administrator")
    _autologin(admin, did, password="")
    _autologin(admin, did, password="x" * 128)
    assert count() == 0
    r = admin.post(f"/displays/{did}/autologin", data={"user": "kiosk", "password": WIN_PASSWORD, "confirm": PASSWORD, "do": "set"},
                   environ_overrides={"REMOTE_ADDR": "10.1.2.3"})
    assert count() == 0                                                   # plain http from another host
    r = admin.post(f"/displays/{did}/autologin", data={"user": "kiosk", "password": WIN_PASSWORD, "confirm": PASSWORD, "do": "set"},
                   environ_overrides={"REMOTE_ADDR": "10.1.2.3", "wsgi.url_scheme": "https"})
    assert count() == 1                                                   # https is fine from anywhere


def test_autologin_needs_an_enrolled_agent_that_can_do_it(app, admin, display):
    _autologin(admin, display)
    with app.app_context():
        assert DeviceCommand.query.count() == 0
    # the generic action route refuses it as well (it has no params)
    assert _ask(admin, display, "set_autologin").status_code in (200, 302)
    with app.app_context():
        assert DeviceCommand.query.count() == 0


def test_autologin_undo_needs_no_password_and_is_signed_for_the_agent(app, admin, enrolled, client):
    did, token, _ = enrolled
    _autologin(admin, did, **{"do": "undo", "password": "", "confirm": ""})
    got = _agent_commands(client, token)["commands"]
    assert json.loads(got[0]["signed"]["payload"])["params"] == {"enabled": False}


def test_removing_the_agent_cancels_waiting_commands_and_their_ciphertext(app, admin, enrolled, client):
    did, token, _ = enrolled
    _autologin(admin, did)
    admin.post(f"/displays/{did}/agent-enroll/remove")
    with app.app_context():
        cmd = DeviceCommand.query.filter_by(action="set_autologin").one()
        assert cmd.state == "cancelled" and cmd.params is None


def test_display_page_shows_the_agent_card_and_never_a_code(app, admin, enrolled):
    html = admin.get(f"/displays/{enrolled[0]}").get_data(as_text=True)
    assert "Privileged agent" in html and "KIOSK1" in html and "Turn on automatic sign-in" in html
    assert CODE_RE.search(html) is None


# --- crypto vectors shared with the Windows agent --------------------------------------------------

def test_committed_vectors_still_decrypt_and_verify():
    """clients/electron-client/test/vectors/agent-vectors.json is what the Node tests check too."""
    from pathlib import Path
    path = Path(__file__).resolve().parents[2] / "clients" / "electron-client" / "test" / "vectors" / "agent-vectors.json"
    if not path.is_file():
        pytest.skip("vectors not generated")
    v = json.loads(path.read_text())
    priv = X25519PrivateKey.from_private_bytes(bytes.fromhex(v["agent_private_hex"]))
    e = v["ecies"]
    assert ecies.decrypt(priv, e["envelope"], e["aad"].encode()) == e["plaintext"].encode("utf-8")


def test_migration_0020_up_and_down(app):
    from flask_migrate import downgrade, upgrade
    from sqlalchemy import inspect
    cols = {"agent_token_hash", "agent_enroll_hash", "agent_enroll_expires", "agent_enroll_fails", "agent_pubkey",
            "agent_diag", "agent_seen_at", "agent_enrolled_at"}
    with app.app_context():
        assert cols <= {c["name"] for c in inspect(db.engine).get_columns("displays")}
        downgrade(revision="a2e6c8f95b13")
        assert not cols & {c["name"] for c in inspect(db.engine).get_columns("displays")}
        upgrade()
        assert cols <= {c["name"] for c in inspect(db.engine).get_columns("displays")}


# --- review fixes ---------------------------------------------------------------------------------

def _queue_autologin(admin, did):
    assert _autologin(admin, did).status_code in (200, 302)


def _raw_agent_state(app, did):
    with app.app_context():
        d = db.session.get(Display, did)
        return (d.agent_token_hash, d.agent_pubkey, d.agent_diag, d.agent_enroll_hash, d.agent_enrolled_at)


def test_unpair_revokes_the_agent_and_open_commands_and_repair_does_not_inherit(app, admin, enrolled, client):
    did, token, _ = enrolled
    _queue_autologin(admin, did)
    _ask(admin, did, "reboot_device")
    assert _agent_commands(client, token)["commands"]
    r = admin.post(f"/displays/{did}/unpair")
    assert r.status_code in (200, 302)
    assert client.get(f"{API}/agent/commands", headers=bearer(token)).status_code == 401
    assert client.post(f"{API}/agent/diagnostics", json={"diag": AGENT_DIAG}, headers=bearer(token)).status_code == 401
    assert _raw_agent_state(app, did) == (None, None, None, None, None)
    with app.app_context():
        assert DeviceCommand.query.filter(DeviceCommand.state.in_(("pending", "delivered"))).count() == 0
        assert all(c.params is None for c in DeviceCommand.query.filter_by(action="set_autologin"))


def test_agent_token_is_refused_when_the_display_is_unpaired_even_if_the_row_survived(app, enrolled, client):
    did, token, _ = enrolled
    with app.app_context():
        db.session.execute(text("UPDATE displays SET token_hash = NULL, paired_at = NULL WHERE id = :i"), {"i": did})
        db.session.commit()
    assert client.get(f"{API}/agent/commands", headers=bearer(token)).status_code == 401


def test_pairing_a_new_device_onto_the_display_drops_the_old_agent(app, admin, enrolled, client):
    from aisignx.displays import pairing
    did, token, _ = enrolled
    _ask(admin, did, "reboot_device")
    with app.app_context():
        row, _secret = pairing.start("10.0.0.5", "newpc", None)
        code = row.code
        db.session.commit()
        d = pairing.approve(code, None, None, db.session.get(Display, did), None)
        assert d is not None
        db.session.commit()
    assert client.get(f"{API}/agent/commands", headers=bearer(token)).status_code == 401
    assert _raw_agent_state(app, did) == (None, None, None, None, None)
    with app.app_context():
        assert DeviceCommand.query.filter(DeviceCommand.state.in_(("pending", "delivered"))).count() == 0


def test_an_expired_autologin_envelope_is_wiped(app, admin, enrolled):
    from aisignx.displays import agent
    did, token, _ = enrolled
    _queue_autologin(admin, did)
    with app.app_context():
        assert "enc" in DeviceCommand.query.filter_by(action="set_autologin").one().params
        agent.expire_due(utcnow() + timedelta(days=1))
        db.session.commit()
        cmd = DeviceCommand.query.filter_by(action="set_autologin").one()
        assert cmd.state == "expired" and cmd.params is None


def test_job_blanks_leftover_envelopes_on_finished_commands(app, admin, enrolled):
    from aisignx.displays import agent
    did, token, _ = enrolled
    _queue_autologin(admin, did)
    with app.app_context():
        cmd = DeviceCommand.query.filter_by(action="set_autologin").one()
        cmd.state = "cancelled"
        db.session.commit()
        agent.job()
        assert DeviceCommand.query.filter_by(action="set_autologin").one().params is None


def test_wrong_codes_cannot_kill_other_displays_codes(app, admin, display, client):
    _priv, pub = _keys()
    code = _code(admin, display)
    for _ in range(enroll.MAX_FAILS + 3):
        agent_api.reset_rate_limit()
        assert _enroll(client, "ABCD-EFGH-JKLM-NPQR", pub).status_code == 403
    assert _enroll(client, code, pub).status_code == 200


def test_autologin_confirm_password_is_rate_limited(app, admin, enrolled):
    did, token, _ = enrolled
    from aisignx.auth import ratelimit
    limit = app.config.get("LOGIN_MAX_PER_USER", 5)
    for _ in range(limit + 2):
        _autologin(admin, did, confirm="wrong")
    page = admin.post(f"/displays/{did}/autologin", follow_redirects=True, data={
        "user": "kiosk", "password": WIN_PASSWORD, "confirm": PASSWORD, "do": "set"}).get_data(as_text=True)
    assert "Too many wrong passwords" in page    # the right password is refused too
    with app.app_context():
        assert DeviceCommand.query.filter_by(action="set_autologin").count() == 0


def test_autologin_refuses_http_through_a_same_host_proxy(app, admin, enrolled):
    did, token, _ = enrolled
    data = {"user": "kiosk", "password": WIN_PASSWORD, "confirm": PASSWORD, "do": "set"}
    admin.post(f"/displays/{did}/autologin", data=data, headers={"X-Forwarded-For": "203.0.113.9"})
    with app.app_context():
        assert DeviceCommand.query.filter_by(action="set_autologin").count() == 0


def test_update_installer_kind_comes_from_the_agent_not_the_player(app, enrolled):
    from aisignx.displays import agent
    from aisignx.downloads import service as dl
    did, token, _ = enrolled
    with app.app_context():
        d = db.session.get(Display, did)
        d.diagnostics = json.dumps({**CAPS, "shell": {"name": "android"}})
        seen = []
        orig = dl.latest_download
        dl.latest_download = lambda kind: seen.append(kind)
        try:
            agent.update_package(d)
        finally:
            dl.latest_download = orig
        assert seen == ["windows-player"]


def test_enroll_body_is_capped_and_audit_carries_the_key_id(app, admin, display, client):
    _priv, pub = _keys()
    big = client.post(f"{API}/agent/enroll", json={"code": "x", "public_key": pub, "pad": "a" * 5000})
    assert big.status_code == 413
    assert _enroll(client, _code(admin, display), pub).status_code == 200
    with app.app_context():
        row = AuditLog.query.filter_by(action="display.agent_enrolled").one()
        assert json.loads(row.detail)["fingerprint"] == ecies.key_id(base64.b64decode(pub))


def test_overlong_utf8_password_gets_an_accurate_message(app, admin, enrolled):
    did, token, _ = enrolled
    r = _autologin(admin, did, password="€" * 127, **{})
    page = admin.post(f"/displays/{did}/autologin", follow_redirects=True, data={
        "user": "kiosk", "password": "€" * 127, "confirm": PASSWORD, "do": "set"}).get_data(as_text=True)
    assert "too long" in page


# --- approval instead of a typed code ---------------------------------------------------------------

CLAIM, POLL = "c" * 22 + "AB", "p" * 22 + "CD"


def _request(client, pub, claim=CLAIM, poll=POLL):
    return client.post(f"{API}/agent/request", json={"claim_id": claim, "poll_secret": poll, "public_key": pub,
                                                     "host": "FRONTDESK", "version": "3.4.0"})


def _open_envelope(priv, ciphertext, claim=CLAIM):
    import hashlib
    aad = b"aisignx-agent-approval|" + hashlib.sha256(f"aisignx-agent-claim|{claim}".encode()).hexdigest().encode()
    return ecies.decrypt(priv, ciphertext, aad)


def test_an_agent_is_approved_by_an_admin_and_collects_its_token_encrypted_to_itself(app, admin, display, client):
    priv, pub = _keys()
    assert _request(client, pub).get_json()["state"] == "pending"
    assert client.post(f"{API}/agent/poll", json={"poll_secret": POLL}).get_json() == {"state": "pending", "claimed": False}
    # the player of the display says "that agent is on my PC"
    r = client.post(f"{API}/agent/claim", json={"claim_id": CLAIM}, headers=bearer(DISPLAY_TOKEN))
    assert r.status_code == 200 and r.get_json()["state"] == "pending"
    page = admin.get(f"/displays/{display}").get_data(as_text=True)
    assert "FRONTDESK" in page and "is waiting for approval" in page
    from aisignx.models import AgentRequest
    with app.app_context():
        rid = AgentRequest.query.one().id
    assert admin.post(f"/displays/{display}/agent-request/{rid}/approve").status_code == 302
    got = client.post(f"{API}/agent/poll", json={"poll_secret": POLL}).get_json()
    assert got["state"] == "approved" and got["display_id"] == display and got["command_key"] and got["fingerprint"]
    token = _open_envelope(priv, got["ciphertext"]).decode()
    assert token.startswith("agt_")
    assert client.get(f"{API}/agent/commands", headers=bearer(token)).status_code == 200     # a working agent credential
    _p2, _pub2 = _keys()
    assert _open_envelope(_p2, got["ciphertext"]) is None                                      # nobody else can read it
    with app.app_context():
        d = db.session.get(Display, display)
        assert d.agent_token_hash == enroll.hash_token(token) and token not in json.dumps(
            [r.ciphertext for r in AgentRequest.query.all()])


def test_pairing_with_the_agent_box_ticked_approves_the_first_claim_by_itself(app, admin, display, client):
    priv, pub = _keys()
    with app.app_context():
        db.session.get(Display, display).agent_auto_approve = True
        db.session.commit()
    _request(client, pub)
    r = client.post(f"{API}/agent/claim", json={"claim_id": CLAIM}, headers=bearer(DISPLAY_TOKEN))
    assert r.get_json()["state"] == "approved"
    got = client.post(f"{API}/agent/poll", json={"poll_secret": POLL}).get_json()
    assert _open_envelope(priv, got["ciphertext"]).decode().startswith("agt_")
    with app.app_context():
        assert db.session.get(Display, display).agent_auto_approve is False            # once only


def test_claims_polls_and_decisions_are_guarded(app, admin, display, client):
    _priv, pub = _keys()
    _request(client, pub)
    assert client.post(f"{API}/agent/claim", json={"claim_id": CLAIM}).status_code == 401           # needs the display's credential
    assert client.post(f"{API}/agent/claim", json={"claim_id": "x" * 30}, headers=bearer(DISPLAY_TOKEN)).status_code == 404
    assert client.post(f"{API}/agent/claim", json={"claim_id": "bad id"}, headers=bearer(DISPLAY_TOKEN)).status_code == 400
    assert client.post(f"{API}/agent/poll", json={"poll_secret": CLAIM}).status_code == 404         # the claim id is not the poll secret
    assert _request(client, pub, claim=CLAIM, poll="q" * 24).status_code == 400                         # a taken claim id cannot be re-used
    assert _request(client, pub, claim=POLL, poll=POLL).status_code == 400
    assert _request(client, "AAAA").status_code == 400
    client.post(f"{API}/agent/claim", json={"claim_id": CLAIM}, headers=bearer(DISPLAY_TOKEN))
    from aisignx.models import AgentRequest
    with app.app_context():
        rid = AgentRequest.query.one().id
    make_user(app, "ed2", "editor")
    ed = app.test_client()
    login(ed, "ed2")
    assert ed.post(f"/displays/{display}/agent-request/{rid}/approve").status_code == 403           # admins only
    assert admin.post(f"/displays/{display}/agent-request/{rid}/maybe").status_code == 404
    assert admin.post(f"/displays/{display}/agent-request/{rid}/reject").status_code == 302
    assert client.post(f"{API}/agent/poll", json={"poll_secret": POLL}).get_json()["state"] == "rejected"
    assert admin.post(f"/displays/{display}/agent-request/{rid}/approve").status_code == 404        # decided already


def test_an_agent_request_cannot_be_claimed_by_two_displays_and_expires(app, admin, display, client):
    from aisignx.models import AgentRequest, Display as D
    from aisignx.player.auth import hash_token
    _priv, pub = _keys()
    _request(client, pub)
    with app.app_context():
        other = D(name="Other", token_hash=hash_token("o" * 40), paired_at=utcnow())
        db.session.add(other)
        db.session.commit()
    assert client.post(f"{API}/agent/claim", json={"claim_id": CLAIM}, headers=bearer(DISPLAY_TOKEN)).status_code == 200
    assert client.post(f"{API}/agent/claim", json={"claim_id": CLAIM}, headers=bearer("o" * 40)).status_code == 409
    with app.app_context():
        r = AgentRequest.query.one()
        r.expires_at = utcnow() - timedelta(minutes=1)
        db.session.commit()
    assert client.post(f"{API}/agent/poll", json={"poll_secret": POLL}).status_code == 404


def test_the_pair_form_offers_the_agent_box_to_admins_only(app, admin, client):
    assert b"privileged agent that the setup program installed" in admin.get("/displays/pair").data
    make_user(app, "ed3", "editor")
    ed = app.test_client()
    login(ed, "ed3")
    assert b"privileged agent that the setup program installed" not in ed.get("/displays/pair").data
