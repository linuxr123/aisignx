"""Clean what the privileged agent reports (POST /agent/diagnostics).

Same rule as diag.py: rebuilt from a whitelist, so nothing unexpected is stored. The agent's public key is NOT
taken from here (it is fixed at enrollment); only its `key_id` is, to show a mismatch.
"""
import json
import re

from .diag import MAX_LEN, _bool, _int, _obj, _str

_HEX16 = re.compile(r"^[0-9a-f]{16}$")


def _hex16(value) -> str | None:
    return value if isinstance(value, str) and _HEX16.match(value) else None


_FIELDS = {
    "version": lambda v: _str(v, 20),
    "installed_version": lambda v: _str(v, 20),
    "state": lambda v: v if v in ("running", "unsafe_acl", "starting") else None,
    "host": lambda v: _str(v, 64),
    "key_id": _hex16,
    "kiosk_user": lambda v: _str(v, 64),
    "can_reboot": _bool, "can_update": _bool, "can_set_startup": _bool, "can_autologin": _bool,
    "startup": lambda v: _obj(v, {"autostart": _bool, "mode": lambda m: m if m in ("app", "shell") else None,
                                  "shell_capable": _bool}),
    "autologin": lambda v: _obj(v, {"enabled": _bool, "user": lambda x: _str(x, 64)}),
    "last_update": lambda v: _obj(v, {"id": _hex16, "state": lambda x: _str(x, 24), "to": lambda x: _str(x, 20)}),
    "command_key_fingerprint": lambda v: _str(v, 24),
    "command_key_changed": _bool,
    "uptime_s": _int,
}


def clean(obj) -> str | None:
    if not isinstance(obj, dict):
        return None
    text = json.dumps(_obj(obj, _FIELDS) or {}, separators=(",", ":"), ensure_ascii=False)
    return text if len(text) <= MAX_LEN else None
