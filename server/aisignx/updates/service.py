"""Ask GitHub for the latest release, remember the answer, and verify signed release files.

Privacy: the check is one anonymous HTTPS GET to the public releases API. It sends no
installation id, no organisation name and no counts. It can be switched off in Settings
(or with AISIGNX_UPDATE_CHECK=0).

Trust: nothing downloaded is trusted because it came from GitHub. A release file is only
accepted when its Ed25519 signature verifies against the public key built into this
program (`update_key.pub`); the private key is held offline by the publisher.
"""
import base64
import json
import logging
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from flask import current_app
from packaging.version import InvalidVersion, Version

from .. import __version__
from ..extensions import db
from ..models import Setting
from ..timeutil import utcnow

log = logging.getLogger(__name__)

DEFAULT_REPO = "linuxr123/aisignx"
MAX_RESPONSE = 1_000_000
TIMEOUT_S = 10
NOTES_MAX = 6000
_REPO = re.compile(r"^[A-Za-z0-9_.-]{1,64}/[A-Za-z0-9_.-]{1,100}$")
KEY_FILE = Path(__file__).with_name("update_key.pub")


def repo() -> str:
    value = (os.environ.get("AISIGNX_UPDATE_REPO") or DEFAULT_REPO).strip()
    return value if _REPO.match(value) else DEFAULT_REPO


def enabled() -> bool:
    if os.environ.get("AISIGNX_UPDATE_CHECK", "").strip().lower() in ("0", "false", "no", "off"):
        return False
    return Setting.get_bool("updates.enabled")


def parse_version(text: str) -> Version | None:
    try:
        return Version(str(text).strip().lstrip("vV"))
    except InvalidVersion:
        return None


def is_newer(latest: str, current: str = __version__) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a and b and a > b)


@dataclass
class Release:
    version: str
    url: str
    notes: str
    published: str
    assets: list


def parse_release(data: dict, repo_name: str) -> Release | None:
    """Pick out only what we use; ignore everything else GitHub sends."""
    if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
        return None
    tag = str(data.get("tag_name") or "")
    if not parse_version(tag):
        return None
    prefix = f"https://github.com/{repo_name}/releases/"
    url = str(data.get("html_url") or "")
    if not url.startswith(prefix):
        url = prefix + "tag/" + tag  # never trust a link that points elsewhere
    assets = []
    for a in data.get("assets") or []:
        name = str(a.get("name") or "")
        link = str(a.get("browser_download_url") or "")
        if name and link.startswith(f"https://github.com/{repo_name}/releases/download/"):
            assets.append({"name": name[:160], "url": link, "size": int(a.get("size") or 0)})
    return Release(version=tag.lstrip("vV"), url=url, notes=str(data.get("body") or "")[:NOTES_MAX],
                   published=str(data.get("published_at") or "")[:32], assets=assets[:20])


def fetch_latest(repo_name: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo_name}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "AiSignX-update-check"})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 (fixed https URL)
        raw = resp.read(MAX_RESPONSE + 1)
    if len(raw) > MAX_RESPONSE:
        raise ValueError("the answer was too large")
    return json.loads(raw)


def check(fetch=fetch_latest) -> str:
    """Ask GitHub now and store the result. Returns a short status; never raises."""
    name = repo()
    try:
        release = parse_release(fetch(name), name)
        Setting.set_datetime("updates.checked_at", utcnow())
        if release is None:
            Setting.set("updates.error", "")
            Setting.set("updates.latest", "")
            db.session.commit()
            return "no published release"
        Setting.set("updates.latest", release.version)
        Setting.set("updates.url", release.url)
        Setting.set("updates.notes", release.notes)
        Setting.set("updates.published", release.published)
        Setting.set("updates.assets", json.dumps(release.assets))
        Setting.set("updates.error", "")
        db.session.commit()
        return f"latest is {release.version}" + (" (newer)" if is_newer(release.version) else "")
    except urllib.error.HTTPError as exc:
        msg = "no releases published yet" if exc.code == 404 else f"GitHub answered {exc.code}"
    except Exception as exc:  # offline, DNS, bad JSON, ...
        msg = f"could not reach GitHub ({type(exc).__name__})"
    db.session.rollback()
    Setting.set_datetime("updates.checked_at", utcnow())
    Setting.set("updates.error", msg)
    db.session.commit()
    return msg


def check_job() -> str | None:
    if not enabled():
        return "update check is off"
    return check()


def status() -> dict:
    latest = Setting.get("updates.latest") or ""
    try:
        assets = json.loads(Setting.get("updates.assets") or "[]")
    except ValueError:
        assets = []
    return {"enabled": enabled(), "current": __version__, "latest": latest,
            "available": bool(latest) and is_newer(latest), "url": Setting.get("updates.url") or "",
            "notes": Setting.get("updates.notes") or "", "published": Setting.get("updates.published") or "",
            "checked_at": Setting.get_datetime("updates.checked_at"), "error": Setting.get("updates.error") or "",
            "assets": assets, "repo": repo(), "key_present": public_key() is not None}


# --- Signatures -------------------------------------------------------------------------------

def public_key() -> Ed25519PublicKey | None:
    """The publisher's key, built into the program. None until one has been generated."""
    try:
        raw = base64.b64decode(KEY_FILE.read_text(encoding="ascii").strip(), validate=True)
        return Ed25519PublicKey.from_public_bytes(raw)
    except (OSError, ValueError):
        return None


def verify(data: bytes, signature_b64: str, key: Ed25519PublicKey | None = None) -> bool:
    """True only for a valid signature by the built-in key (or `key`, for tests)."""
    key = key or public_key()
    if key is None:
        return False
    try:
        key.verify(base64.b64decode(signature_b64.strip(), validate=True), data)
        return True
    except (InvalidSignature, ValueError):
        return False
