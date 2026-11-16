"""Which app build is the newest on the Downloads page, and is a screen's app older than it?"""
from packaging.version import InvalidVersion, Version

from ..models import Download

# The app name a player reports (diagnostics shell.name) -> the Downloads kind that holds its installer.
SHELL_KINDS = {"android": "android-player", "electron": "windows-player"}


def _parse(text) -> Version | None:
    try:
        return Version(str(text or "").strip().lstrip("vV"))
    except InvalidVersion:
        return None


def latest_version(kind: str) -> str | None:
    best = None
    for (v,) in Download.query.with_entities(Download.version).filter_by(kind=kind):
        parsed = _parse(v)
        if parsed and (best is None or parsed > best[0]):
            best = (parsed, v.strip())
    return best[1] if best else None


def app_update(shell) -> dict | None:
    """{"current", "latest", "kind"} when this screen's app is older than the newest installer
    published for it; None when it is current, unknown or has no installer on the page."""
    if not isinstance(shell, dict):
        return None
    kind = SHELL_KINDS.get(str(shell.get("name") or ""))
    current = _parse(shell.get("version"))
    if not kind or current is None:
        return None
    latest = latest_version(kind)
    parsed = _parse(latest)
    if parsed is None or parsed <= current:
        return None
    return {"current": str(shell.get("version")), "latest": latest, "kind": kind}
