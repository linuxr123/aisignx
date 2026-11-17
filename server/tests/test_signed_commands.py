"""Tier B device commands: signed by the server, params typed, installer served only while a command is open."""
import base64
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from sqlalchemy import update

from aisignx.extensions import db
from aisignx.models import DeviceCommand, Display, Download

from .conftest import DISPLAY_TOKEN, bearer, login, make_user

API = "/api/player/v1"
SHA = "ab" * 32
CAPS = {"agent": {"version": "3.1.0", "tier": "plain", "can_restart": True, "can_collect_logs": True,
                  "can_reboot": True, "can_set_startup": True, "can_update": True,
                  "startup": {"autostart": False, "mode": "app", "shell_capable": True}},
        "shell": {"name": "electron", "version": "3.1.0"}}


def _setup(app, paired_display, caps=CAPS, **flags):
    with app.app_context():
        d = db.session.get(Display, paired_display[0])
        d.agent_allow_reboot = flags.get("reboot", True)
        d.agent_allow_startup = flags.get("startup", True)
        d.agent_allow_update = flags.get("update", True)
        d.diagnostics = json.dumps(caps) if caps is not None else None
        db.session.commit()
    return paired_display[0]


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _post(admin, did, action, **form):
    return admin.post(f"/displays/{did}/agent/{action}", data=form)


def _verify(client, item):
    pub = client.get(f"{API}/command-key", headers=bearer(DISPLAY_TOKEN)).get_json()["public_key"]
    key = Ed25519PublicKey.from_public_bytes(base64.b64decode(pub))
    key.verify(base64.b64decode(item["signed"]["sig"]), item["signed"]["payload"].encode("utf-8"))
    return json.loads(item["signed"]["payload"])


def test_command_key_is_stable_private_and_needs_a_display_token(app, configured, client, paired_display):
    assert client.get(f"{API}/command-key").status_code in (401, 403)
    first = client.get(f"{API}/command-key", headers=bearer(DISPLAY_TOKEN)).get_json()
    again = client.get(f"{API}/command-key", headers=bearer(DISPLAY_TOKEN)).get_json()
    assert first == again and len(base64.b64decode(first["public_key"])) == 32
    assert len(first["fingerprint"].split()) == 4
    from pathlib import Path
    assert (Path(app.instance_path) / "command_key").is_file()


def test_reboot_is_signed_for_exactly_this_display_and_tampering_fails(app, configured, client, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "reboot_device")
    body = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()
    item, = body["commands"]
    assert item["action"] == "reboot_device" and body["server_time_ms"] > 0
    payload = _verify(client, item)
    assert payload["display_id"] == did and payload["request_id"] == item["id"]
    assert payload["action"] == "reboot_device" and payload["params"] == {} and payload["v"] == 1
    assert payload["exp_ms"] > body["server_time_ms"] and payload["seq"] > 0
    forged = dict(item["signed"], payload=item["signed"]["payload"].replace("reboot_device", "update_app"))
    with pytest.raises(InvalidSignature):
        pub = client.get(f"{API}/command-key", headers=bearer(DISPLAY_TOKEN)).get_json()["public_key"]
        Ed25519PublicKey.from_public_bytes(base64.b64decode(pub)).verify(
            base64.b64decode(forged["sig"]), forged["payload"].encode("utf-8"))


def test_tier_a_commands_are_not_signed(app, configured, client, paired_display, admin):
    did = _setup(app, paired_display)
    with app.app_context():
        db.session.get(Display, did).agent_allow_restart = True
        db.session.commit()
    _setup_restart = _post(admin, did, "restart_player")
    item, = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]
    assert "signed" not in item


def test_each_tier_b_command_needs_its_own_switch_and_the_device_ability(app, paired_display, admin):
    did = _setup(app, paired_display, reboot=False)
    _post(admin, did, "reboot_device")
    with app.app_context():
        assert DeviceCommand.query.count() == 0
    caps = json.loads(json.dumps(CAPS))
    caps["agent"]["can_reboot"] = False
    _setup(app, paired_display, caps=caps)
    _post(admin, did, "reboot_device")
    with app.app_context():
        assert DeviceCommand.query.count() == 0
    _setup(app, paired_display)
    _post(admin, did, "reboot_device")
    with app.app_context():
        assert DeviceCommand.query.count() == 1


def test_no_second_reboot_within_ten_minutes(app, configured, client, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "reboot_device")
    client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN))
    with app.app_context():
        cmd = DeviceCommand.query.one()
        rid = cmd.request_id
    client.post(f"{API}/commands/{rid}/ack", json={"state": "done", "result": "rebooting"}, headers=bearer(DISPLAY_TOKEN))
    _post(admin, did, "reboot_device")
    with app.app_context():
        assert DeviceCommand.query.count() == 1


def test_set_startup_params_are_typed_and_shell_needs_a_capable_device(app, configured, client, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "set_startup")                                   # nothing chosen
    _post(admin, did, "set_startup", autostart="maybe", mode="root")   # junk is dropped, so nothing chosen
    with app.app_context():
        assert DeviceCommand.query.count() == 0
    _post(admin, did, "set_startup", autostart="on", mode="shell")
    item, = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]
    assert _verify(client, item)["params"] == {"autostart": True, "mode": "shell"}
    caps = json.loads(json.dumps(CAPS))
    caps["agent"]["startup"]["shell_capable"] = False
    _setup(app, paired_display, caps=caps)
    with app.app_context():
        DeviceCommand.query.delete()
        db.session.commit()
    _post(admin, did, "set_startup", mode="shell")
    with app.app_context():
        assert DeviceCommand.query.count() == 0                        # the device cannot undo a shell, so no


def _download(app, version="3.1.1", kind="windows-player", data=b"MZ" + b"z" * 400):
    import hashlib
    from aisignx.downloads.routes import downloads_dir
    with app.app_context():
        name = "stored" + version.replace(".", "")
        (downloads_dir() / name).write_bytes(data)
        db.session.add(Download(kind=kind, version=version, filename="Setup.exe", stored_name=name, size=len(data),
                                sha256=hashlib.sha256(data).hexdigest()))
        db.session.commit()
    return data


def test_update_app_uses_the_server_chosen_installer_and_serves_it_only_while_open(app, configured, client,
                                                                              paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "update_app")                                    # no installer on the page yet
    with app.app_context():
        assert DeviceCommand.query.count() == 0
    data = _download(app)
    _post(admin, did, "update_app", version="9.9.9", sha256="00" * 32, size="5")   # request values are ignored
    with app.app_context():
        cmd = DeviceCommand.query.one()
        rid = cmd.request_id
        assert cmd.params["version"] == "3.1.1" and cmd.params["size"] == len(data)
    # not delivered yet: the package is not available
    assert client.get(f"{API}/commands/{rid}/package", headers=bearer(DISPLAY_TOKEN)).status_code == 409
    item, = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]
    signed = _verify(client, item)
    assert signed["params"]["sha256"] == cmd_sha(app, rid)
    got = client.get(f"{API}/commands/{rid}/package", headers=bearer(DISPLAY_TOKEN))
    assert got.status_code == 200 and got.data == data
    assert client.get(f"{API}/commands/nope/package", headers=bearer(DISPLAY_TOKEN)).status_code == 404
    client.post(f"{API}/commands/{rid}/ack", json={"state": "done", "result": "installing 3.1.1"},
                headers=bearer(DISPLAY_TOKEN))
    assert client.get(f"{API}/commands/{rid}/package", headers=bearer(DISPLAY_TOKEN)).status_code == 409  # closed


def cmd_sha(app, rid):
    with app.app_context():
        return DeviceCommand.query.filter_by(request_id=rid).one().params["sha256"]


def test_display_page_offers_the_push_when_the_installer_is_newer(app, paired_display, admin):
    did = _setup(app, paired_display)
    _download(app, version="3.1.1")
    with app.app_context():
        db.session.execute(update(Display).where(Display.id == did).values(diagnostics=json.dumps(CAPS)))
        db.session.commit()
    page = admin.get(f"/displays/{did}").get_data(as_text=True)
    assert "Push the update" in page and "Update available: 3.1.1" in page
    assert "Reboot the device" in page and "Start the player at sign-in" in page


def test_displays_list_asks_about_pushing_and_about_a_missing_installer(app, configured, paired_display, admin):
    did = _setup(app, paired_display)
    _download(app, version="3.1.1")
    page = admin.get("/displays/").get_data(as_text=True)
    assert "run an older Windows app" in page and "3.1.1" in page            # device runs 3.1.0
    with app.app_context():                                                   # now the device is AHEAD of the page
        d = db.session.get(Display, did)
        caps = json.loads(d.diagnostics)
        caps["shell"]["version"] = "3.2.0"
        db.session.execute(update(Display).where(Display.id == did).values(diagnostics=json.dumps(caps)))
        db.session.commit()
    with app.app_context():
        from aisignx.downloads.service import fleet_updates
        # a second, older screen is what makes "ahead" worth saying
        other = Display(name="Other", token_hash="x" * 64, diagnostics=json.dumps({"shell": {"name": "electron", "version": "3.0.0"}}))
        db.session.add(other)
        db.session.commit()
        rows = fleet_updates(Display.query.all())
        assert rows and rows[0]["ahead"] == "3.2.0" and "Other" in rows[0]["outdated"]
    page = admin.get("/displays/").get_data(as_text=True)
    assert "newer than what the" in page and "3.2.0" in page


def test_seq_is_time_based_and_strictly_increasing_even_if_ids_restart(app, configured, client, paired_display, admin):
    did = _setup(app, paired_display)
    _post(admin, did, "set_startup", autostart="on")
    _post(admin, did, "reboot_device")
    with app.app_context():
        a, b = DeviceCommand.query.order_by(DeviceCommand.id).all()
        assert a.seq > 1_700_000_000_000 and b.seq > a.seq            # milliseconds since 1970, not 1, 2, 3
        # a restored database hands out low ids again: the seq must still be higher than before
        a.seq, b.seq = 5_000_000_000_000, 5_000_000_000_001
        db.session.commit()
    items = client.get(f"{API}/commands", headers=bearer(DISPLAY_TOKEN)).get_json()["commands"]
    assert sorted(_verify(client, i)["seq"] for i in items) == [5_000_000_000_000, 5_000_000_000_001]
