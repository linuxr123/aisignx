"""Reading an APK's signing key, and refusing a push that Android would refuse (a different signing key)."""
import hashlib
import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

from aisignx.displays import agent
from aisignx.downloads import apksign
from aisignx.extensions import db
from aisignx.models import Display, Download
from aisignx.player.auth import hash_token
from aisignx.timeutil import utcnow

CERT_A, CERT_B = b"certificate-A-der" * 5, b"certificate-B-der" * 5


def _block(cert: bytes, scheme: int = 0x7109871A, extra_pairs: bytes = b"") -> bytes:
    certs = struct.pack("<I", len(cert)) + cert
    signed = struct.pack("<I", 0) + struct.pack("<I", len(certs)) + certs + struct.pack("<I", 0)        # digests, certificates, attributes
    signer = struct.pack("<I", len(signed)) + signed + struct.pack("<I", 0) + struct.pack("<I", 0)       # signed data, signatures, public key
    value = struct.pack("<I", len(signer) + 4) + struct.pack("<I", len(signer)) + signer
    pair = struct.pack("<Q", len(value) + 4) + struct.pack("<I", scheme) + value
    pairs = extra_pairs + pair
    size = len(pairs) + 24
    return struct.pack("<Q", size) + pairs + struct.pack("<Q", size) + b"APK Sig Block 42"


def _apk(cert=None, scheme=0x7109871A, extra=b"") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", b"manifest")
        z.writestr("classes.dex", b"dex" * 100)
    data = buf.getvalue()
    if cert is None:
        return data
    eocd = data.rfind(b"PK\x05\x06")
    cd = struct.unpack_from("<I", data, eocd + 16)[0]
    block = _block(cert, scheme, extra)
    patched = bytearray(data[:cd] + block + data[cd:])
    struct.pack_into("<I", patched, eocd + len(block) + 16, cd + len(block))
    return bytes(patched)


@pytest.mark.parametrize("scheme", [0x7109871A, 0xF05368C0, 0x1B93AD61])
def test_the_key_is_the_sha256_of_the_first_certificate(tmp_path, scheme):
    f = tmp_path / "a.apk"
    f.write_bytes(_apk(CERT_A, scheme))
    assert apksign.signer_sha256(f) == hashlib.sha256(CERT_A).hexdigest()
    assert zipfile.ZipFile(f).testzip() is None                       # still a valid zip


def test_other_blocks_before_the_signature_are_skipped(tmp_path):
    other = struct.pack("<Q", 8) + struct.pack("<I", 0x42726577) + b"padd"
    f = tmp_path / "a.apk"
    f.write_bytes(_apk(CERT_B, extra=other))
    assert apksign.signer_sha256(f) == hashlib.sha256(CERT_B).hexdigest()


@pytest.mark.parametrize("make", [lambda: _apk(None), lambda: b"not a zip at all", lambda: b"", lambda: _apk(CERT_A)[:-10]])
def test_anything_else_gives_none_never_an_error(tmp_path, make):
    f = tmp_path / "x.apk"
    f.write_bytes(make())
    assert apksign.signer_sha256(f) is None
    assert apksign.signer_sha256(tmp_path / "missing.apk") is None


def test_a_garbled_block_gives_none(tmp_path):
    data = bytearray(_apk(CERT_A))
    i = data.find(b"APK Sig Block 42")
    data[i - 8:i] = struct.pack("<Q", 10 ** 12)                       # an absurd size
    f = tmp_path / "g.apk"
    f.write_bytes(bytes(data))
    assert apksign.signer_sha256(f) is None


def _display(app, signer, token="t" * 40):
    with app.app_context():
        d = Display(name="Tab", token_hash=hash_token(token), paired_at=utcnow(), agent_allow_update=True)
        d.diagnostics = json.dumps({"shell": {"name": "android", "version": "3.0.5"},
                                    "agent": {"version": "3.0.5", "can_update": True, "tier": "plain", "signer": signer} if signer else
                                    {"version": "3.0.5", "can_update": True, "tier": "plain"}})
        db.session.add(d)
        db.session.commit()
        return d.id


def _publish(app, cert):
    from aisignx.downloads.routes import downloads_dir
    with app.app_context():
        data = _apk(cert)
        name = "apk" + hashlib.sha256(data).hexdigest()[:20]
        (downloads_dir() / name).write_bytes(data)
        db.session.add(Download(kind="android-player", filename="AiSignX-Player-3.0.7.apk", stored_name=name, version="3.0.7",
                                size=len(data), sha256=hashlib.sha256(data).hexdigest()))
        db.session.commit()


def test_a_push_with_another_signing_key_is_refused_in_words(app, configured):
    _publish(app, CERT_B)
    did = _display(app, hashlib.sha256(CERT_A).hexdigest())
    with app.app_context():
        cmd, reason = agent.create(db.session.get(Display, did), "update_app", None)
        assert cmd is None and "different signing key" in reason and "Reinstall" in reason


def test_the_same_key_unknown_keys_and_old_apps_are_not_blocked(app, configured):
    _publish(app, CERT_A)
    same = _display(app, hashlib.sha256(CERT_A).hexdigest())
    with app.app_context():
        cmd, reason = agent.create(db.session.get(Display, same), "update_app", None)
        assert cmd is not None and reason is None
    with app.app_context():
        old = Display(name="Old app", token_hash=hash_token("o" * 40), paired_at=utcnow(), agent_allow_update=True,
                      diagnostics=json.dumps({"shell": {"name": "android", "version": "3.0.5"}, "agent": {"can_update": True, "tier": "plain"}}))
        db.session.add(old)
        db.session.commit()
        assert agent.signer_problem(old, None) is None
        assert agent.signer_problem(old, db.session.query(Download).first()) is None            # no reported key: cannot tell, so it is tried


def test_the_reported_key_must_be_64_hex_characters():
    from aisignx.player.diag import _agent
    assert _agent({"signer": "AB" * 32})["signer"] == "ab" * 32
    assert _agent({"signer": "zz"}) is None and _agent({"signer": 5}) is None                      # nothing valid left: dropped


def _publish_as(app, cert, version, filename):
    from aisignx.downloads.routes import downloads_dir
    with app.app_context():
        data = _apk(cert) + version.encode()          # trailing bytes after the zip: another file, same key
        name = "apk" + hashlib.sha256(data).hexdigest()[:20]
        (downloads_dir() / name).write_bytes(data)
        row = Download(kind="android-player", filename=filename, stored_name=name, version=version,
                       size=len(data), sha256=hashlib.sha256(data).hexdigest())
        db.session.add(row)
        db.session.commit()
        return row.id


def test_each_screen_gets_the_installer_signed_with_its_own_key(app, configured):
    from aisignx.displays import updates
    from aisignx.downloads.service import app_update, fleet_updates
    a_id = _publish_as(app, CERT_A, "3.0.7", "AiSignX-Player-3.0.7-debug.apk")
    b_id = _publish_as(app, CERT_B, "3.0.9", "AiSignX-Player-3.0.9.apk")
    key_a, key_b = hashlib.sha256(CERT_A).hexdigest(), hashlib.sha256(CERT_B).hexdigest()
    on_a, on_b, unknown = _display(app, key_a, "a" * 40), _display(app, key_b.upper(), "b" * 40), _display(app, None, "u" * 40)
    on_c = _display(app, hashlib.sha256(b"another key").hexdigest(), "c" * 40)
    with app.app_context():
        d = lambda i: db.session.get(Display, i)
        assert agent.update_package(d(on_a)).id == a_id                 # the older installer, but the only one Android accepts
        assert agent.update_package(d(on_b)).id == b_id                 # the reported key is compared without case
        assert agent.update_package(d(unknown)).id == b_id              # no key reported: the newest, as before
        assert agent.update_package(d(on_c)).id == b_id                 # no installer with its key: the newest, refused in words
        cmd, reason = agent.create(d(on_a), "update_app", None)
        assert reason is None and cmd.params["version"] == "3.0.7"
        cmd, reason = agent.create(d(on_c), "update_app", None)
        assert cmd is None and "different signing key" in reason
        shell = {"name": "android", "version": "3.0.5"}
        assert app_update(shell, key_a)["latest"] == "3.0.7" and app_update(shell, None)["latest"] == "3.0.9"
        assert app_update({"name": "android", "version": "3.0.7"}, key_a) is None
        by_name = {r["display"].id: r for r in updates.rows(Display.query.all())}
        assert by_name[on_a]["latest"] == "3.0.7" and by_name[on_b]["latest"] == "3.0.9"
        fleet = fleet_updates(Display.query.all())
        assert fleet[0]["latest"] == "3.0.7, 3.0.9" and len(fleet[0]["outdated"]) == 4 and fleet[0]["ahead"] is None


def test_the_displays_banner_finds_a_screen_newer_than_its_own_installer(app, configured):
    from aisignx.downloads.service import fleet_updates
    _publish_as(app, CERT_A, "3.0.7", "AiSignX-Player-3.0.7-debug.apk")
    _publish_as(app, CERT_B, "3.0.9", "AiSignX-Player-3.0.9.apk")
    newer = _display(app, hashlib.sha256(CERT_A).hexdigest(), "a" * 40)
    _display(app, hashlib.sha256(CERT_B).hexdigest(), "b" * 40)
    with app.app_context():
        d = db.session.get(Display, newer)
        d.diagnostics = d.diagnostics.replace('"version": "3.0.5"}, "agent"', '"version": "3.0.8"}, "agent"')
        db.session.commit()
        fleet = fleet_updates(Display.query.all())[0]
        assert fleet["ahead"] == "3.0.8" and fleet["ahead_has"] == "3.0.7"     # 3.0.8 < the other key's 3.0.9, still reported
        assert fleet["latest"] == "3.0.9" and len(fleet["outdated"]) == 1


def test_adding_an_apk_replaces_the_older_one_with_the_same_key_only(app, configured, tmp_path):
    runner = app.test_cli_runner()
    files = {}
    for name, cert, version in (("AiSignX-Player-3.0.7-debug.apk", CERT_A, "3.0.7"), ("AiSignX-Player-3.0.8.apk", CERT_B, "3.0.8"),
                                ("AiSignX-Player-3.0.9-debug.apk", CERT_A, "3.0.9")):
        f = tmp_path / name
        f.write_bytes(_apk(cert) + version.encode())
        files[name] = f
        result = runner.invoke(args=["downloads", "add", str(f), "--kind", "android-player", "--version", version])
        assert result.exit_code == 0, result.output
    with app.app_context():
        assert sorted(r.filename for r in Download.query.all()) == ["AiSignX-Player-3.0.8.apk", "AiSignX-Player-3.0.9-debug.apk"]
    result = runner.invoke(args=["downloads", "add", str(files["AiSignX-Player-3.0.8.apk"]), "--kind", "android-player",
                                 "--version", "3.0.8", "--keep"])
    with app.app_context():
        assert Download.query.count() == 3                               # --keep removes nothing


def test_adding_an_apk_keeps_the_one_an_open_update_still_downloads(app, configured, tmp_path):
    from aisignx.models import DeviceCommand
    runner = app.test_cli_runner()
    old = tmp_path / "AiSignX-Player-3.0.7-debug.apk"
    old.write_bytes(_apk(CERT_A) + b"3.0.7")
    assert runner.invoke(args=["downloads", "add", str(old), "--kind", "android-player", "--version", "3.0.7"]).exit_code == 0
    did = _display(app, hashlib.sha256(CERT_A).hexdigest())
    with app.app_context():
        cmd, reason = agent.create(db.session.get(Display, did), "update_app", None)
        assert reason is None
        db.session.commit()
    new = tmp_path / "AiSignX-Player-3.0.8-debug.apk"
    new.write_bytes(_apk(CERT_A) + b"3.0.8")
    result = runner.invoke(args=["downloads", "add", str(new), "--kind", "android-player", "--version", "3.0.8"])
    assert result.exit_code == 0 and "Kept AiSignX-Player-3.0.7-debug.apk" in result.output
    with app.app_context():
        assert Download.query.count() == 2 and DeviceCommand.query.count() == 1


def test_the_updates_page_shows_the_refusal_reason(app, configured):
    from aisignx.displays import updates
    _publish(app, CERT_B)
    did = _display(app, hashlib.sha256(CERT_A).hexdigest())
    with app.app_context():
        rows = updates.rows(Display.query.all())
        assert rows[0]["status"] == "outdated" and not rows[0]["can_push"] and "signing key" in rows[0]["reason"]
