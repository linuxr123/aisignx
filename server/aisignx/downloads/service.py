"""Which app build is the newest on the Downloads page, and is a screen's app older than it?"""
import json
from functools import lru_cache

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


def latest_download(kind: str, ext: str | None = None):
    """The Download row with the highest version of this kind (versions compared as versions), or None. Files without
    a version (the agent script, a certificate) count as the lowest; among equals the newest upload wins.
    ext: only files with this extension (a kind can hold several files, for example the agent zip and its script)."""
    best = None
    zero = Version("0")
    for row in Download.query.filter_by(kind=kind).all():
        if ext and not row.filename.lower().endswith("." + ext.lower()):
            continue
        key = (_parse(row.version) or zero, row.id)
        if best is None or key > best[0]:
            best = (key, row)
    return best[1] if best else None


def _apk_signer(row) -> str | None:
    """The signing key of an APK on the page (cached per stored file, which never changes), or None."""
    from .routes import downloads_dir

    if not row.filename.lower().endswith(".apk"):
        return None
    path = downloads_dir() / row.stored_name
    try:
        st = path.stat()
    except OSError:
        return None
    return _signer_cached(str(path), st.st_size, st.st_mtime_ns)


@lru_cache(maxsize=64)
def _signer_cached(path: str, _size: int, _mtime_ns: int) -> str | None:
    from . import apksign
    return apksign.signer_sha256(path)


def display_signer(display) -> str | None:
    """The signing key the screen's Android app reported (diagnostics "agent" block), lowercase, or None."""
    try:
        agent = (json.loads(display.diagnostics or "{}") or {}).get("agent")
    except (TypeError, ValueError, AttributeError):
        return None
    signer = agent.get("signer") if isinstance(agent, dict) else None
    return signer.lower() if isinstance(signer, str) and signer else None


def installer_for(kind: str, signer: str | None = None):
    """The installer a screen gets. Android refuses an update signed with another key, and the page may hold one Android
    installer per key (a debug and a release build): a screen that reported its key gets the newest APK signed with that
    key. No key reported, another kind, or no APK with that key: the newest installer of the kind (then
    displays.agent.signer_problem explains a key mismatch in words)."""
    if kind != SHELL_KINDS["android"] or not signer:
        return latest_download(kind)
    best = None
    zero = Version("0")
    for row in Download.query.filter_by(kind=kind).all():
        if _apk_signer(row) != signer.lower():
            continue
        key = (_parse(row.version) or zero, row.id)
        if best is None or key > best[0]:
            best = (key, row)
    return best[1] if best else latest_download(kind)


def app_update(shell, signer: str | None = None) -> dict | None:
    """{"current", "latest", "kind"} when this screen's app is older than the newest installer
    published for it (for Android: signed with its key, see installer_for); None when it is current,
    unknown or has no installer on the page."""
    if not isinstance(shell, dict):
        return None
    kind = SHELL_KINDS.get(str(shell.get("name") or ""))
    current = _parse(shell.get("version"))
    if not kind or current is None:
        return None
    row = installer_for(kind, signer)
    parsed = _parse(row.version) if row else None
    if parsed is None or parsed <= current:
        return None
    return {"current": str(shell.get("version")), "latest": row.version.strip(), "kind": kind}


KIND_LABELS = {"android-player": "Android app", "windows-player": "Windows app"}


def fleet_updates(displays) -> list[dict]:
    """What the Displays list says about app versions, one entry per app kind that needs attention:
    {"kind", "label", "latest", "outdated": [display names], "ahead": version or None}.
    "outdated": screens running an app older than the newest installer on the Downloads page (they can be
    updated). "ahead": a screen already runs a version newer than the page has (the admin updated one by
    hand, or tested a build); the installer must be uploaded before the others can be pushed to it. Each screen is
    compared with its own installer (installer_for: an Android app with the installer signed with its key)."""
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
        published = installer_for(kind, display_signer(d))
        pub_v = _parse(published.version) if published else None
        seen.setdefault(kind, []).append((d, version, str(shell.get("version")), pub_v, published.version.strip() if pub_v else None))
    out = []
    for kind, running in seen.items():
        outdated = [d.name for d, v, _t, pub_v, _p in running if pub_v is not None and v < pub_v]
        # The installers the outdated screens would get (two when a debug and a release build are listed side by side).
        targets = sorted({(pub_v, text) for _d, v, _t, pub_v, text in running if pub_v is not None and v < pub_v})
        # A screen newer than its own installer, while other screens run something older than it.
        newer = sorted(((v, t, p) for _d, v, t, pub_v, p in running if pub_v is None or v > pub_v), key=lambda x: x[0])
        ahead, ahead_has = None, None
        if newer and any(v < newer[-1][0] for _d, v, *_rest in running):
            ahead, ahead_has = newer[-1][1], newer[-1][2]
        if outdated or ahead:
            out.append({"kind": kind, "label": KIND_LABELS.get(kind, kind), "latest": ", ".join(t for _v, t in targets) or None,
                        "outdated": outdated, "ahead": ahead, "ahead_has": ahead_has})
    return out
