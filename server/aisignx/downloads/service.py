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


def latest_download(kind: str):
    """The Download row with the highest version of this kind (versions compared as versions), or None."""
    best = None
    for row in Download.query.filter_by(kind=kind).all():
        parsed = _parse(row.version)
        if parsed and (best is None or parsed > best[0]):
            best = (parsed, row)
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


KIND_LABELS = {"android-player": "Android app", "windows-player": "Windows app"}


def fleet_updates(displays) -> list[dict]:
    """What the Displays list says about app versions, one entry per app kind that needs attention:
    {"kind", "label", "latest", "outdated": [display names], "ahead": version or None}.
    "outdated": screens running an app older than the newest installer on the Downloads page (they can be
    updated). "ahead": a screen already runs a version newer than the page has (the admin updated one by
    hand, or tested a build); the installer must be uploaded before the others can be pushed to it."""
    import json

    seen: dict[str, dict] = {}
    for d in displays:
        try:
            shell = (json.loads(d.diagnostics or "{}") or {}).get("shell")
        except (TypeError, ValueError, AttributeError):
            continue
        kind = SHELL_KINDS.get(str((shell or {}).get("name") or ""))
        version = _parse((shell or {}).get("version"))
        if not kind or version is None:
            continue
        entry = seen.setdefault(kind, {"running": [], "top": None})
        entry["running"].append((d, version))
        if entry["top"] is None or version > entry["top"][0]:
            entry["top"] = (version, str(shell.get("version")))
    out = []
    for kind, entry in seen.items():
        published = latest_download(kind)
        pub_v = _parse(published.version) if published else None
        outdated = [d.name for d, v in entry["running"] if pub_v is not None and v < pub_v]
        top_v, top_text = entry["top"]
        ahead = top_text if (pub_v is None or top_v > pub_v) and any(v < top_v for _d, v in entry["running"]) else None
        if outdated or ahead:
            out.append({"kind": kind, "label": KIND_LABELS.get(kind, kind), "latest": published.version if published else None,
                        "outdated": outdated, "ahead": ahead})
    return out
