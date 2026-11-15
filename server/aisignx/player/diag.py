"""Clean the `diag` object a player sends with its heartbeat.

The object is rebuilt from a whitelist, so nothing unexpected is ever stored: unknown
keys are dropped, strings are truncated and integers clamped. The result is stored as
JSON (at most MAX_LEN characters) and only ever shown autoescaped in the admin.
"""
import json

MAX_LEN = 4096
MAX_INT = 2**53
MAX_ERRORS = 10
MAX_CAPS = 8
# Upper bound for device timestamps in ms (1 Jan 3000 UTC): anything later cannot be a
# real clock reading and could not be rendered in every time zone.
MAX_TIME_MS = 32_503_680_000_000


def _int(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value:  # NaN
        return None
    try:
        return max(0, min(MAX_INT, int(value)))
    except (OverflowError, ValueError):  # inf
        return MAX_INT if value > 0 else 0


def _sint(value) -> int | None:
    """A signed integer, clamped to +-one day in ms (a clock offset)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    try:
        return max(-86_400_000, min(86_400_000, int(value)))
    except (OverflowError, ValueError):
        return None


def _num(value) -> float | None:
    """A small positive float (device pixel ratio)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return None
    return round(max(0.0, min(16.0, float(value))), 3)


def _str(value, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _obj(value, fields: dict) -> dict | None:
    """Rebuild a dict from {key: cleaner}; None if value is not a dict or ends up empty."""
    if not isinstance(value, dict):
        return None
    out = {}
    for key, cleaner in fields.items():
        cleaned = cleaner(value.get(key))
        if cleaned is not None:
            out[key] = cleaned
    return out or None


def _caps(value) -> list | None:
    if not isinstance(value, list):
        return None
    return [c[:20] for c in value if isinstance(c, str)][:MAX_CAPS]


def _errors(value) -> list | None:
    if not isinstance(value, list):
        return None
    out = []
    for entry in value[-MAX_ERRORS:]:
        if isinstance(entry, dict) and isinstance(entry.get("msg"), str):
            t = _int(entry.get("t")) or 0
            out.append({"t": t if t <= MAX_TIME_MS else 0, "msg": entry["msg"][:200]})
    return out


def _size(value):
    return _obj(value, {"w": _int, "h": _int})


MAX_SUPPORT = 20


def _bool(value) -> bool | None:
    return value if isinstance(value, bool) else None


def _agent(value) -> dict | None:
    """What the device's agent says about itself: its version, privilege tier and what it can do
    (the admin page hides what the device cannot), and what its watchdog has been doing."""
    return _obj(value, {
        "version": lambda x: _str(x, 20), "tier": lambda x: x if x in ("plain", "device_owner", "admin") else None,
        "can_restart": _bool, "can_collect_logs": _bool, "can_reboot": _bool, "can_update": _bool,
        "watchdog": _bool, "restarts_1h": _int, "gave_up": _bool, "last_restart_ms": _int,
    })


def _support(value) -> dict | None:
    """Which of the playlist's videos this screen says it cannot decode smoothly:
    {"checked": n, "problems": [{"id": media id, "s": "no" | "rough"}]}."""
    if not isinstance(value, dict):
        return None
    checked = _int(value.get("checked"))
    problems = []
    for entry in (value.get("problems") if isinstance(value.get("problems"), list) else [])[:MAX_SUPPORT]:
        if isinstance(entry, dict) and entry.get("s") in ("no", "rough"):
            ident = _int(entry.get("id"))
            if ident:
                problems.append({"id": ident, "s": entry["s"]})
    if checked is None:
        return None
    return {"checked": checked, "problems": problems}


_FIELDS = {
    "ua": lambda v: _str(v, 300),
    "build": lambda v: _str(v, 40),
    "page_uptime_s": _int,
    "screen": lambda v: _obj(v, {"w": _int, "h": _int, "dpr": _num}),
    "viewport": _size,
    "shell": lambda v: _obj(v, {"name": lambda x: _str(x, 20),
                                "version": lambda x: _str(x, 20),
                                "uptime_s": _int, "caps": _caps,
                                "lock": lambda x: _str(x, 16)}),
    "cache": lambda v: _obj(v, {"files": _int, "bytes": _int}),
    "storage_free_bytes": _int,
    "errors": _errors,
    "media_support": _support,
    "sync": lambda v: _obj(v, {"offset_ms": _sint, "rtt_ms": _int}),
    "agent": _agent,
}


def clean(obj) -> str | None:
    """Whitelisted JSON text of a diag object, or None (not a dict, or too large)."""
    if not isinstance(obj, dict):
        return None
    cleaned = _obj(obj, _FIELDS) or {}
    text = json.dumps(cleaned, separators=(",", ":"), ensure_ascii=False)
    if len(text) > MAX_LEN:
        return None
    return text
