"""Per-display options: the manifest's "settings" object and the kiosk unlock PIN.

The PIN is stored only as a salted PBKDF2-SHA256 hash. The native apps verify it when
someone tries to leave them; the browser player never uses it. 4-8 digits can be
brute-forced offline from a stolen manifest, so it is a passer-by kiosk lock, not an
account password.
"""
import hashlib
import hmac
import json
import re
import secrets

from ..models import Display
from ..models.display import ORIENTATIONS

ALG = "pbkdf2-sha256"
PBKDF2_ITER = 200_000
_HASH_RE = re.compile(r"^pbkdf2-sha256\$(\d{1,7})\$([0-9a-f]{32})\$([0-9a-f]{64})$")


def _derive(pin: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations).hex()


def hash_pin(pin: str, salt: bytes | None = None, iterations: int = PBKDF2_ITER) -> str:
    """The column value for a PIN: pbkdf2-sha256$<iter>$<salt hex 32>$<hash hex 64>."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    return f"{ALG}${iterations}${salt.hex()}${_derive(pin, salt, iterations)}"


def set_unlock_pin(display: Display, pin: str) -> None:
    """ORM write, so the commit pushes the new manifest. The caller commits."""
    display.unlock_pin_hash = hash_pin(pin)


def clear_unlock_pin(display: Display) -> None:
    display.unlock_pin_hash = None


def _parse_hash(stored: str | None):
    match = _HASH_RE.match(stored or "")
    if match is None:
        return None
    iterations = int(match.group(1))
    if iterations < 1:
        return None
    return iterations, match.group(2), match.group(3)


def unlock_payload(display: Display) -> dict | None:
    """{"alg", "iter", "salt", "hash"} for the manifest, or None (no PIN or malformed)."""
    parsed = _parse_hash(display.unlock_pin_hash)
    if parsed is None:
        return None
    iterations, salt, digest = parsed
    return {"alg": ALG, "iter": iterations, "salt": salt, "hash": digest}


def verify_pin(stored: str | None, pin: str) -> bool:
    """Check a PIN against a column value. Only tests use it; the native apps verify."""
    parsed = _parse_hash(stored)
    if parsed is None:
        return False
    iterations, salt, digest = parsed
    return hmac.compare_digest(_derive(pin, bytes.fromhex(salt), iterations), digest)


def settings_payload(display: Display) -> dict:
    """The manifest's "settings" object. Location and description never go in it."""
    orientation = display.orientation if display.orientation in ORIENTATIONS else 0
    volume = display.volume if isinstance(display.volume, int) else 100
    return {
        "orientation": orientation,
        "volume": max(0, min(100, volume)),
        "show_offline_banner": bool(display.show_offline_banner),
        "allow_input": bool(display.allow_input),
        "unlock": unlock_payload(display),
    }


def parse_diag(text: str | None) -> dict | None:
    """The stored diagnostics JSON as a dict, for the detail page. None on any error."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
