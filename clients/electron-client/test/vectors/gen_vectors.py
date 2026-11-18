"""Writes agent-vectors.json: fixed keys and nonces so the Python server code and the Node agent code are
checked against the same bytes. Run from server/ with its venv:

    PYTHONPATH=. .venv/Scripts/python ../clients/electron-client/test/vectors/gen_vectors.py

The keys here are test-only constants; they protect nothing.
"""
import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from aisignx.displays import ecies
from aisignx.displays.signing import payload_for

RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)
agent_priv_raw = bytes(range(1, 33))
eph_raw = bytes(range(101, 133))
nonce = bytes(range(201, 213))
agent = X25519PrivateKey.from_private_bytes(agent_priv_raw)
agent_pub = agent.public_key().public_bytes(*RAW)
DISPLAY, RID, USER, DOMAIN = 7, "0123456789abcdef", "kiosk", "."
PASSWORD = "Sup3r secret ü!"
aad = ecies.aad(DISPLAY, RID, USER, DOMAIN)
envelope = ecies.encrypt(agent_pub, PASSWORD.encode("utf-8"), aad, eph=X25519PrivateKey.from_private_bytes(eph_raw), nonce=nonce)

seed = bytes(range(50, 82))
cmd_key = Ed25519PrivateKey.from_private_bytes(seed)
cmd_pub = base64.b64encode(cmd_key.public_key().public_bytes(*RAW)).decode()
EXP = datetime.fromtimestamp(1_800_000_000, tz=timezone.utc)


def signed(request_id, action, params, seq):
    cmd = SimpleNamespace(display_id=DISPLAY, request_id=request_id, action=action, params=params, seq=seq, id=seq,
                          expires_at=EXP)
    payload = payload_for(cmd)
    return {"id": request_id, "action": action, "params": {},
            "signed": {"payload": payload, "sig": base64.b64encode(cmd_key.sign(payload.encode())).decode()}}


out = {
    "agent_private_hex": agent_priv_raw.hex(), "agent_public_b64": base64.b64encode(agent_pub).decode(),
    "key_id": ecies.key_id(agent_pub),
    "ecies": {"display_id": DISPLAY, "request_id": RID, "user": USER, "domain": DOMAIN, "aad": aad.decode(),
              "plaintext": PASSWORD, "envelope": envelope},
    "command_key_b64": cmd_pub, "display_id": DISPLAY, "exp_ms": 1_800_000_000_000,
    "commands": {
        "reboot": signed("1111111111111111", "reboot_device", {}, 1001),
        "update": signed("2222222222222222", "update_app", {"version": "3.2.0", "sha256": "ab" * 32, "size": 1234}, 1002),
        "autologin": signed(RID, "set_autologin",
                            {"enabled": True, "user": USER, "domain": DOMAIN, "key_id": ecies.key_id(agent_pub), "enc": envelope}, 1003),
        "autologin_off": signed("3333333333333333", "set_autologin", {"enabled": False}, 1004),
    },
}
Path(__file__).with_name("agent-vectors.json").write_text(json.dumps(out, indent=1) + "\n")
print("wrote", Path(__file__).with_name("agent-vectors.json"))
