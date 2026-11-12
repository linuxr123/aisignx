"""Jinja filters used across templates."""
from .timeutil import to_local


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


def register(app) -> None:
    app.add_template_filter(filesize, "filesize")
    app.add_template_filter(duration, "duration")
    app.add_template_filter(localtime, "localtime")
