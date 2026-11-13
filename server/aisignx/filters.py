"""Jinja filters used across templates."""
from datetime import datetime, timezone

from .timeutil import iso_z, to_local


def filesize(num_bytes) -> str:
    if num_bytes is None:
        return "—"
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def duration(seconds) -> str:
    """Format seconds as m:ss or h:mm:ss."""
    if seconds is None:
        return "—"
    total = int(round(float(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def localtime(dt, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """Format a stored (UTC) datetime in the organization time zone, with the zone
    abbreviation, e.g. "2026-11-11 11:00 CET"."""
    if dt is None:
        return "—"
    local = to_local(dt)
    return f"{local.strftime(fmt)} {local.tzname()}"


def ms_time(ms, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format a Unix time in milliseconds (as players report it) like localtime."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)) or ms <= 0:
        return "—"
    # Device-reported times can be anything; a value near year 9999 converts to UTC
    # but overflows when shifted to a zone ahead of UTC, so convert inside the try.
    try:
        return localtime(datetime.fromtimestamp(ms / 1000, tz=timezone.utc), fmt)
    except (OverflowError, OSError, ValueError):
        return "—"


def register(app) -> None:
    app.add_template_filter(filesize, "filesize")
    app.add_template_filter(duration, "duration")
    app.add_template_filter(localtime, "localtime")
    app.add_template_filter(ms_time, "ms_time")
    app.add_template_filter(iso_z, "iso_z")
