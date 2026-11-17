"""The server's command key: signs Tier B device commands (docs/signed-commands.md).

One Ed25519 key per server, created on first use and kept in the instance folder (`command_key`,
owner-only). Devices pin its public key the first time they see it, so a stolen display token, or a
request that merely reaches the API, cannot make a device reboot, change its startup or install anything.
It does not defend against someone who controls the server itself; that is stated in the docs.
"""
import base64
import hashlib
import json
import os
import threading
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from flask import current_app

from ..timeutil import as_utc

_lock = threading.Lock()
_cache: dict = {}


def _key_path() -> Path:
    return Path(current_app.instance_path) / "command_key"


def private_key() -> Ed25519PrivateKey:
    """The server's key; made (atomically, owner-only) if it does not exist yet."""
    path = _key_path()
    with _lock:
        stamp = (str(path), path.stat().st_mtime_ns if path.exists() else None)
        hit = _cache.get("key")
        if hit and hit[0] == stamp:
            return hit[1]
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            seed = Ed25519PrivateKey.generate().private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
            tmp = path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(base64.b64encode(seed).decode("ascii") + "\n")
            os.replace(tmp, path)
        key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(path.read_text().strip()))
        _cache["key"] = ((str(path), path.stat().st_mtime_ns), key)
        return key


def public_key_b64() -> str:
    raw = private_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def fingerprint() -> str:
    """16 hex characters of the SHA-256 of the raw public key, in groups of four."""
    digest = hashlib.sha256(base64.b64decode(public_key_b64())).hexdigest()[:16]
    return " ".join(digest[i:i + 4] for i in range(0, 16, 4))


def payload_for(cmd) -> str:
    """The exact text that is signed (JSON, sorted keys, no spaces). The device verifies the signature over
    these bytes, then reads the fields from them."""
    body = {"v": 1, "display_id": cmd.display_id, "request_id": cmd.request_id, "action": cmd.action,
            "params": cmd.params or {}, "seq": cmd.id,
            "exp_ms": int(as_utc(cmd.expires_at).timestamp() * 1000)}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sign(cmd) -> dict:
    payload = payload_for(cmd)
    sig = private_key().sign(payload.encode("utf-8"))
    return {"payload": payload, "sig": base64.b64encode(sig).decode("ascii")}
