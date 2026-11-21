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


def _display(app, signer):
    with app.app_context():
        d = Display(name="Tab", token_hash=hash_token("t" * 40), paired_at=utcnow(), agent_allow_update=True)
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


def test_the_updates_page_shows_the_refusal_reason(app, configured):
    from aisignx.displays import updates
    _publish(app, CERT_B)
    did = _display(app, hashlib.sha256(CERT_A).hexdigest())
    with app.app_context():
        rows = updates.rows(Display.query.all())
        assert rows[0]["status"] == "outdated" and not rows[0]["can_push"] and "signing key" in rows[0]["reason"]
