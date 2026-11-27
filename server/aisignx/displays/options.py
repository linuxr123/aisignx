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
import unicodedata

from ..models import Display
from ..models.display import ORIENTATIONS, RESUME_DEFAULT_S, RESUME_MAX_S, RESUME_MIN_S

ALG = "pbkdf2-sha256"
PBKDF2_ITER = 200_000
_PIN_RE = re.compile(r"[0-9]{4,8}")
_HASH_RE = re.compile(r"^pbkdf2-sha256\$(\d{1,7})\$([0-9a-f]{32})\$([0-9a-f]{64})$")


def _derive(pin: str, salt: bytes, iterations: int) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode("utf-8"), salt, iterations).hex()


def hash_pin(pin: str, salt: bytes | None = None, iterations: int = PBKDF2_ITER) -> str:
    """The column value for a PIN: pbkdf2-sha256$<iter>$<salt hex 32>$<hash hex 64>."""
    salt = salt if salt is not None else secrets.token_bytes(16)
    return f"{ALG}${iterations}${salt.hex()}${_derive(pin, salt, iterations)}"


def normalize_pin(pin):
    """Fold full-width digits (from an IME) to ASCII and trim whitespace; None stays None."""
    if not isinstance(pin, str):
        return pin
    return unicodedata.normalize("NFKC", pin).strip()


def is_valid_pin(pin) -> bool:
    """4-8 ASCII digits: the only thing the native unlock keypads can type."""
    return isinstance(pin, str) and _PIN_RE.fullmatch(pin) is not None


def set_unlock_pin(display: Display, pin: str) -> None:
    """ORM write, so the commit pushes the new manifest. The caller commits."""
    if not is_valid_pin(pin):
        raise ValueError("unlock PIN must be 4 to 8 ASCII digits")
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
        "brightness": clamp_percent(display.brightness),
        "dim": dim_payload(display),
        "orientation": orientation,
        "volume": max(0, min(100, volume)),
        "show_offline_banner": bool(display.show_offline_banner),
        "allow_input": bool(display.allow_input),
        "group_controls": display.group_controls is not False,  # None: not saved yet, on like the column default
        "kiosk_lock": bool(display.kiosk_lock),
        "resume_after_s": resume_seconds(display.resume_after_s),
        "unlock": unlock_payload(display),
        # The device agent reads these in the native apps (docs/device-agent-design.md).
        "agent": {"watchdog": bool(display.agent_watchdog),
                  "restart": bool(display.agent_allow_restart),
                  "logs": bool(display.agent_allow_logs),
                  "reboot": bool(display.agent_allow_reboot),
                  "startup": bool(display.agent_allow_startup),
                  "update": bool(display.agent_allow_update)},
    }


def clamp_percent(value, low: int = 10) -> int:
    """A brightness in percent: 10..100 (never fully black), 100 when it is not a number."""
    if isinstance(value, bool) or not isinstance(value, int):
        return 100
    return max(low, min(100, value))


def valid_clock(text) -> bool:
    """HH:MM, 00:00..23:59."""
    import re
    return isinstance(text, str) and bool(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", text))


def dim_payload(display: Display) -> dict | None:
    """The night dimming for the manifest, or None when it is off: {level, from, to, tz}. Times are the organization's local time."""
    if not (valid_clock(display.dim_from) and valid_clock(display.dim_to)) or display.dim_from == display.dim_to:
        return None
    from ..timeutil import org_tz

    return {"level": clamp_percent(display.dim_level), "from": display.dim_from, "to": display.dim_to, "tz": org_tz().key}


def resume_seconds(value) -> int:
    """Auto-resume delay for the manifest: clamped to 60..3600, 300 if not an integer."""
    if isinstance(value, bool) or not isinstance(value, int):
        return RESUME_DEFAULT_S
    return max(RESUME_MIN_S, min(RESUME_MAX_S, value))


def support_problems(diag: dict | None) -> list[dict]:
    """[{id, s}] the player reported as not decodable smoothly ("no" or "rough"); [] if none."""
    ms = diag.get("media_support") if isinstance(diag, dict) else None
    problems = ms.get("problems") if isinstance(ms, dict) else None
    if not isinstance(problems, list):
        return []
    return [p for p in problems if isinstance(p, dict) and isinstance(p.get("id"), int)
            and p.get("s") in ("no", "rough")]


def parse_diag(text: str | None) -> dict | None:
    """The stored diagnostics JSON as a dict, for the detail page. None on any error."""
    if not text:
        return None
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None
