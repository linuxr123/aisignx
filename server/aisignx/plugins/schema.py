"""Plugin config fields: definitions (checked when plugin.json loads) and validation.

A field is a dict from plugin.json:

    {name, type, label, help, default, required, min, max, max_length,
     options: [{value, label}], pattern, private, show_if: {field: value}}

Types and what they store:

    string       str, trimmed, max_length (default 200), optional pattern (full match)
    text         str, line breaks kept, max_length (default 5000)
    integer      int, min/max required in the definition; empty means the default
    boolean      bool; absent means false (an unchecked checkbox)
    select       str, one of the option values (no default: the first option)
    url          str, http or https only, no user:password@, at most 2048 characters
    timezone     str, "" (the organization time zone) or an IANA name
    location     {q, lat, lon, label}: lat/lon rounded to 4 decimals
    media_multi  [int], ids of image media, order kept, at most `max` (default 200)

The server validates every field whatever `show_if` says (that only hides inputs in the
form). Unknown keys are dropped, and the stored JSON is capped at 16 KB. `private`
fields never leave the server: public_config() removes them before anything reaches a
screen, the manifest or the token API.
"""
import json
import math
import re
from functools import lru_cache
from urllib.parse import urlsplit
from zoneinfo import available_timezones

FIELD_TYPES = ("string", "text", "integer", "boolean", "select", "url", "timezone",
               "location", "media_multi", "image_source")
RESERVED = frozenset({"name", "duration", "loop", "video_duration", "enabled"})
NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
MAX_CONFIG_BYTES = 16 * 1024
MAX_URL = 2048
MAX_LOCATION_TEXT = 100
MEDIA_MULTI_MAX = 200
GLOBAL_ERROR = "_"  # errors key for problems that belong to no single field

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_LATLON = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*$")
_TRUE = {"1", "true", "on", "yes", "y"}
_FALSE = {"0", "false", "off", "no", "n", ""}
_DEFAULT_MAX_LENGTH = {"string": 200, "text": 5000}


class SchemaError(ValueError):
    """A bad field definition (at load) or a config value a hook refuses (normalize)."""

    def __init__(self, message: str, field: str | None = None):
        super().__init__(message)
        self.field = field


class _Invalid(Exception):
    pass


@lru_cache(maxsize=1)
def _zones() -> frozenset:
    return frozenset(available_timezones())


def timezone_names() -> list[str]:
    """Every IANA zone name, sorted (for the form's time zone picker)."""
    return sorted(_zones())


# --- definitions ------------------------------------------------------------------------

def _int_or_fail(value, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaError(f"{what} must be a whole number")
    return value


def _options(raw, name: str) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        raise SchemaError(f"field {name!r}: a select needs a non-empty options list")
    out, seen = [], set()
    for opt in raw:
        if isinstance(opt, str):
            opt = {"value": opt, "label": opt}
        if not isinstance(opt, dict) or not isinstance(opt.get("value"), str):
            raise SchemaError(f"field {name!r}: each option needs a string value")
        if opt["value"] in seen:
            raise SchemaError(f"field {name!r}: duplicate option {opt['value']!r}")
        seen.add(opt["value"])
        out.append({**opt, "label": str(opt.get("label") or opt["value"])})
    return out


def _check_field(raw, names: set) -> dict:
    if not isinstance(raw, dict):
        raise SchemaError("each field must be an object")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SchemaError(f"bad field name {name!r} (use ^[a-z][a-z0-9_]{{0,31}}$)")
    if name in RESERVED:
        raise SchemaError(f"field name {name!r} is reserved")
    if name in names:
        raise SchemaError(f"duplicate field {name!r}")
    ftype = raw.get("type")
    if ftype not in FIELD_TYPES:
        raise SchemaError(f"field {name!r}: unknown type {ftype!r}")
    field = dict(raw)
    field["label"] = str(raw.get("label") or name.replace("_", " ").capitalize())
    field["help"] = str(raw.get("help") or "")
    field["required"] = bool(raw.get("required", False))
    field["private"] = bool(raw.get("private", False))
    if ftype in _DEFAULT_MAX_LENGTH:
        max_length = raw.get("max_length", _DEFAULT_MAX_LENGTH[ftype])
        if _int_or_fail(max_length, f"field {name!r}: max_length") < 1:
            raise SchemaError(f"field {name!r}: max_length must be at least 1")
        field["max_length"] = max_length
    if raw.get("pattern") is not None:
        if ftype != "string":
            raise SchemaError(f"field {name!r}: only string fields take a pattern")
        try:
            re.compile(raw["pattern"])
        except (re.error, TypeError) as exc:
            raise SchemaError(f"field {name!r}: bad pattern ({exc})") from None
    if ftype == "integer":
        lo = _int_or_fail(raw.get("min"), f"field {name!r}: min")
        hi = _int_or_fail(raw.get("max"), f"field {name!r}: max")
        if lo > hi:
            raise SchemaError(f"field {name!r}: min is above max")
    if ftype == "media_multi":
        field["max"] = _int_or_fail(raw.get("max", MEDIA_MULTI_MAX), f"field {name!r}: max")
        if not 1 <= field["max"] <= MEDIA_MULTI_MAX:
            raise SchemaError(f"field {name!r}: max must be 1..{MEDIA_MULTI_MAX}")
    if ftype == "select":
        field["options"] = _options(raw.get("options"), name)
        if raw.get("default") is None:
            field["default"] = field["options"][0]["value"]
    show_if = raw.get("show_if")
    if show_if is not None and not isinstance(show_if, dict):
        raise SchemaError(f"field {name!r}: show_if must be an object")
    if raw.get("default") is not None:
        try:
            _coerce(field, raw["default"], check_refs=False)
        except _Invalid as exc:
            raise SchemaError(f"field {name!r}: bad default ({exc})") from None
    return field


def check_fields(raw) -> list[dict]:
    """Validate a list of field definitions; returns normalized copies.
    Raises SchemaError on the first problem."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SchemaError("schema/settings must be a list of fields")
    fields, names = [], set()
    for item in raw:
        field = _check_field(item, names)
        names.add(field["name"])
        fields.append(field)
    for field in fields:
        for other in (field.get("show_if") or {}):
            if other not in names:
                raise SchemaError(f"field {field['name']!r}: show_if names unknown field "
                                  f"{other!r}")
    return fields


# --- values -----------------------------------------------------------------------------

def _empty(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _text(field, value, multiline: bool) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        raise _Invalid("Must be text.")
    if multiline:
        value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.strip()
    if _CONTROL.search(value if multiline else value.replace("\n", "\x00")):
        raise _Invalid("Contains characters that are not allowed.")
    if len(value) > field["max_length"]:
        raise _Invalid(f"At most {field['max_length']} characters.")
    if value and field.get("pattern") and not re.fullmatch(field["pattern"], value):
        raise _Invalid("This value is not in the expected format.")
    return value


def _integer(field, value) -> int:
    if isinstance(value, bool):
        raise _Invalid("Enter a whole number.")
    if isinstance(value, float):
        if not value.is_integer():
            raise _Invalid("Enter a whole number.")
        value = int(value)
    elif isinstance(value, str):
        try:
            value = int(value.strip())
        except ValueError:
            raise _Invalid("Enter a whole number.") from None
    elif not isinstance(value, int):
        raise _Invalid("Enter a whole number.")
    if not field["min"] <= value <= field["max"]:
        raise _Invalid(f"Must be between {field['min']} and {field['max']}.")
    return value


def _boolean(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.strip().lower() in _TRUE | _FALSE:
        return value.strip().lower() in _TRUE
    raise _Invalid("Must be on or off.")


def check_url(value) -> str:
    """An http(s) URL without user info; raises _Invalid. Shared with the settings."""
    if not isinstance(value, str):
        raise _Invalid("Enter an http or https address.")
    value = value.strip()
    if len(value) > MAX_URL:
        raise _Invalid(f"At most {MAX_URL} characters.")
    if _CONTROL.search(value) or any(c.isspace() for c in value):
        raise _Invalid("Enter an http or https address.")
    try:
        parts = urlsplit(value)
        port = parts.port  # raises ValueError for a bad port
    except ValueError:
        raise _Invalid("Enter an http or https address.") from None
    del port
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise _Invalid("Enter an http or https address.")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise _Invalid("Addresses with a user name or password are not allowed.")
    return value


def _float(value, lo: float, hi: float, what: str) -> float:
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (TypeError, ValueError):
        raise _Invalid(f"{what} must be a number.") from None
    if isinstance(value, bool) or not math.isfinite(number) or not lo <= number <= hi:
        raise _Invalid(f"{what} must be between {lo:g} and {hi:g}.")
    return round(number, 4)


def _short(value) -> str:
    text = value.strip() if isinstance(value, str) else ""
    return _CONTROL.sub("", text)[:MAX_LOCATION_TEXT]


def parse_latlon(text) -> tuple[float, float] | None:
    """(lat, lon) from "52.37, 4.89"; None when it is not that shape or out of range."""
    match = _LATLON.match(text or "") if isinstance(text, str) else None
    if not match:
        return None
    try:
        return (_float(match.group(1), -90, 90, "Latitude"),
                _float(match.group(2), -180, 180, "Longitude"))
    except _Invalid:
        return None


def _location(value) -> dict:
    if isinstance(value, str):
        value = {"q": value}
    if not isinstance(value, dict):
        raise _Invalid("Choose a place.")
    q = _short(value.get("q"))
    lat, lon = value.get("lat"), value.get("lon")
    if _empty(lat) and _empty(lon):
        pair = parse_latlon(q)
        if pair is None:
            raise _Invalid("Choose a place with Find, or enter latitude,longitude.")
        lat, lon = pair
    lat = _float(lat, -90, 90, "Latitude")
    lon = _float(lon, -180, 180, "Longitude")
    label = _short(value.get("label")) or q or f"{lat:g}, {lon:g}"
    return {"q": q, "lat": lat, "lon": lon, "label": label}


def _media_multi(field, value, check_refs: bool) -> list[int]:
    if isinstance(value, str):
        value = [v for v in value.split(",") if v.strip()]
    if not isinstance(value, (list, tuple)):
        raise _Invalid("Choose images from the library.")
    ids = []
    for item in value:
        try:
            if isinstance(item, bool):
                raise ValueError
            number = int(item.strip()) if isinstance(item, str) else int(item)
        except (TypeError, ValueError):
            raise _Invalid("Choose images from the library.") from None
        if number > 0 and number not in ids:
            ids.append(number)
    if len(ids) > field["max"]:
        raise _Invalid(f"Choose at most {field['max']} images.")
    if check_refs and ids:
        from ..models import Media
        from ..models.media import MEDIA_IMAGE

        found = {mid for (mid,) in Media.query.with_entities(Media.id).filter(
            Media.id.in_(ids), Media.media_type == MEDIA_IMAGE)}
        if len(found) != len(ids):
            raise _Invalid("Some selected images no longer exist or are not images.")
    return ids


def _coerce(field, value, check_refs: bool):
    """The stored value for one field; raises _Invalid with a message for the form."""
    ftype = field["type"]
    if ftype == "boolean":
        return False if value is None else _boolean(value)
    if ftype == "location" and isinstance(value, dict) and all(
            _empty(value.get(k)) for k in ("q", "lat", "lon")):
        value = None  # an untouched location input
    if _empty(value) or (ftype == "media_multi" and value in ([], ())):
        default = field.get("default")
        if field["required"] and default is None:
            raise _Invalid("This field is required.")
        if default is not None and ftype not in ("location", "media_multi"):
            value = default
        else:
            return {"location": None, "media_multi": [], "integer": None}.get(ftype, "")
    if ftype in ("string", "text"):
        result = _text(field, value, multiline=ftype == "text")
        if field["required"] and not result:
            raise _Invalid("This field is required.")
        return result
    if ftype == "integer":
        return _integer(field, value)
    if ftype == "select":
        allowed = [o["value"] for o in field["options"]]
        if str(value) not in allowed:
            raise _Invalid("Choose one of the listed options.")
        return str(value)
    if ftype == "image_source":
        text = str(value).strip()
        if not text.isdigit() or int(text) <= 0:
            raise _Invalid("Choose one of the image sources.")
        if check_refs:
            from ..models import ImageSource
            from ..extensions import db
            if db.session.get(ImageSource, int(text)) is None:
                raise _Invalid("That image source no longer exists.")
        return int(text)
    if ftype == "url":
        return check_url(value)
    if ftype == "timezone":
        name = str(value).strip()
        if name and name not in _zones():
            raise _Invalid("Unknown time zone.")
        return name
    if ftype == "location":
        return _location(value)
    return _media_multi(field, value, check_refs)


def validate(fields: list[dict], raw, check_refs: bool = True) -> tuple[dict, dict]:
    """(config, errors) for a raw dict (typed JSON or form strings).

    errors maps a field name (or GLOBAL_ERROR) to a message; config is only meaningful
    when errors is empty. check_refs=False skips database checks (media_multi ids exist);
    the manifest path uses it so a deleted image never invalidates a whole item.
    """
    raw = raw if isinstance(raw, dict) else {}
    config, errors = {}, {}
    for field in fields:
        try:
            config[field["name"]] = _coerce(field, raw.get(field["name"]), check_refs)
        except _Invalid as exc:
            errors[field["name"]] = str(exc)
    if not errors and config_size(config) > MAX_CONFIG_BYTES:
        errors[GLOBAL_ERROR] = "These settings are too large (16 KB at most)."
    return config, errors


def config_size(config) -> int:
    return len(json.dumps(config, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def validate_value(field: dict, value, check_refs: bool = True):
    """(value, error) for one field."""
    try:
        return _coerce(field, value, check_refs), None
    except _Invalid as exc:
        return None, str(exc)


def public_config(fields: list[dict], config: dict) -> dict:
    """The config without private fields (what screens and the token API may see)."""
    private = {f["name"] for f in fields if f["private"]}
    return {k: v for k, v in (config or {}).items() if k not in private}


def defaults(fields: list[dict]) -> dict:
    """A config holding every field's default (or its empty value)."""
    out = {}
    for field in fields:
        value, error = validate_value(field, field.get("default"), check_refs=False)
        out[field["name"]] = value if error is None else (
            False if field["type"] == "boolean" else
            {"location": None, "media_multi": [], "integer": None}.get(field["type"], ""))
    return out


def from_form(fields: list[dict], form) -> dict:
    """Raw values from a submitted form (a werkzeug MultiDict) for validate().

    Field inputs are named after the field; a location uses <name>.q, <name>.lat,
    <name>.lon and <name>.label; a media_multi sends one <name> input per id.
    """
    raw = {}
    for field in fields:
        name, ftype = field["name"], field["type"]
        if ftype == "boolean":
            raw[name] = form.get(name, False) if name in form else False
        elif ftype == "location":
            raw[name] = {part: form.get(f"{name}.{part}", "")
                         for part in ("q", "lat", "lon", "label")}
        elif ftype == "media_multi":
            raw[name] = form.getlist(name)
        else:
            raw[name] = form.get(name)
    return raw


def to_setting(field: dict, value) -> str:
    """How an organization setting value is stored in a Setting row (a string)."""
    if field["type"] == "boolean":
        return "1" if value else "0"
    if field["type"] in ("location", "media_multi"):
        return json.dumps(value, separators=(",", ":"))
    return "" if value is None else str(value)


def from_setting(field: dict, text):
    """A stored Setting string back to the field's raw value (for validate_value)."""
    if text is None:
        return None
    if field["type"] in ("location", "media_multi"):
        try:
            return json.loads(text)
        except ValueError:
            return None
    return text
