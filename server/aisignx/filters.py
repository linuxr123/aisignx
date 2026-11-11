"""Jinja filters used across templates."""


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


def register(app) -> None:
    app.add_template_filter(filesize, "filesize")
    app.add_template_filter(duration, "duration")
