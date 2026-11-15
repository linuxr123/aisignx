"""Web pages that refuse to be shown inside another page.

Many sites (google.com, most dashboards, anything behind a login) send X-Frame-Options or a
Content-Security-Policy "frame-ancestors" rule that forbids embedding. In the player's frame
such a page shows only an error. This module:

  * checks a page's response headers for those rules (once a day), and
  * takes a Full HD screenshot of the page with the server's browser (media/webshot.py), at the
    interval the editor chose (default 5 minutes), for pages that cannot be embedded or that the
    editor wants as a screenshot. The player then simply shows an image.

Media.web_mode: "auto" (live unless the site forbids it), "live", "screenshot".
The screenshot is a file in the media folder (Media.snapshot_name); the manifest presents a
screenshot web page as an image item (player/manifest.py). The check and the screenshots run in
the media.webpages job and, right after a page is added or changed, in a background thread.
"""
from __future__ import annotations

import logging
import re
import shutil
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path

from flask import current_app

from ..extensions import db
from ..models import Media
from ..models.media import MEDIA_WEB
from ..timeutil import as_utc, utcnow
from . import storage, webshot

log = logging.getLogger(__name__)

SNAPSHOT_SIZE = (1920, 1080)
CHECK_EVERY = timedelta(days=1)
MIN_REFRESH_S, MAX_REFRESH_S, DEFAULT_REFRESH_S = 60, 86400, 300
PER_RUN = 2                      # screenshots per job run, so one run stays short
CHECK_TIMEOUT_S = 10
MODES = ("auto", "live", "screenshot")
_lock = threading.Lock()         # one screenshot at a time: a browser start is heavy


def blocks_embedding(headers: list[tuple[str, str]]) -> bool:
    """True when the response headers forbid showing the page in a frame. headers: (name, value)
    pairs; names are case-insensitive. Pure, so it is tested without a network."""
    for name, value in headers:
        lname = name.lower()
        if lname == "x-frame-options":
            tokens = {t.strip().lower() for t in value.split(",")}
            if tokens & {"deny", "sameorigin"}:
                return True
        elif lname == "content-security-policy":
            for directive in value.split(";"):
                parts = directive.strip().split()
                if parts and parts[0].lower() == "frame-ancestors":
                    # Allowed only by a wildcard; anything else lists who may embed (not us)
                    # or none ('none', 'self').
                    if "*" not in parts[1:]:
                        return True
    return False


def check_embeddable(url: str) -> bool | None:
    """Whether the page can be shown in a frame; None when it could not be reached (unknown).
    Reads only the response headers, never the body."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AiSignX"}, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=CHECK_TIMEOUT_S)  # noqa: S310 (http/https only)
    except urllib.error.HTTPError as exc:  # 403 and friends still carry the headers
        resp = exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.info("embeddable check of %s failed: %s", url, exc)
        return None
    try:
        return not blocks_embedding(list(resp.headers.items()))
    finally:
        resp.close()


def clamp_refresh(value) -> int:
    try:
        return max(MIN_REFRESH_S, min(MAX_REFRESH_S, int(value)))
    except (TypeError, ValueError):
        return DEFAULT_REFRESH_S


def _due_check(media: Media, now) -> bool:
    if media.web_mode not in (None, "auto"):
        return False
    return media.web_checked_at is None or now - as_utc(media.web_checked_at) >= CHECK_EVERY


def _due_snapshot(media: Media, now) -> bool:
    if not media.shows_snapshot:
        return False
    if not media.snapshot_name or media.snapshot_at is None:
        return True
    return now - as_utc(media.snapshot_at) >= timedelta(seconds=clamp_refresh(media.web_refresh_s))


def needs_work(media: Media, now=None) -> bool:
    now = now or utcnow()
    return media.media_type == MEDIA_WEB and (_due_check(media, now) or _due_snapshot(media, now))


def refresh_snapshot(media: Media) -> bool:
    """Take a new screenshot. True when one was saved; the old file is removed. A screenshot
    identical to the last one is dropped, so a static page does not make screens re-download."""
    if not webshot.browser_path():
        return False
    with tempfile.TemporaryDirectory(prefix="snap-", dir=storage.work_dir(),
                                     ignore_cleanup_errors=True) as tmp:
        shot = Path(tmp) / "shot.png"
        if not webshot.capture(media.url, shot, size=SNAPSHOT_SIZE):
            return False
        size, checksum = storage.size_and_sha256(shot)
        if checksum == media.snapshot_sha256 and media.snapshot_name:
            media.snapshot_at = utcnow()
            db.session.commit()
            return True
        name = storage.new_stored_name("png")
        shutil.move(str(shot), storage.media_dir() / name)
    old = media.snapshot_name
    media.snapshot_name, media.snapshot_sha256, media.snapshot_size = name, checksum, size
    media.snapshot_at = utcnow()
    db.session.commit()
    storage.remove_file(storage.media_dir(), old)
    return True


def process(media_id: int) -> str:
    """Do whatever is due for one web page: the embeddable check, then the screenshot."""
    media = db.session.get(Media, media_id)
    if media is None or media.media_type != MEDIA_WEB:
        return ""
    now, did = utcnow(), []
    if _due_check(media, now):
        result = check_embeddable(media.url)
        if result is not None:
            media.web_embeddable = result
        media.web_checked_at = now  # unreachable: try again tomorrow (or on the next edit)
        db.session.commit()
        did.append("checked")
    if _due_snapshot(media, now):
        with _lock:
            did.append("screenshot" if refresh_snapshot(media) else "screenshot failed")
    return ", ".join(did)


def reset(media: Media, url_changed: bool) -> None:
    """After an edit: check the page again, and drop a screenshot of another address."""
    media.web_checked_at = None
    if url_changed:
        media.web_embeddable = None
        old, media.snapshot_name = media.snapshot_name, None
        media.snapshot_sha256 = media.snapshot_size = media.snapshot_at = None
        db.session.commit()
        storage.remove_file(storage.media_dir(), old)


def queue(media: Media) -> None:
    """Check the page and take its screenshot soon, off the request. Call after committing."""
    app = current_app._get_current_object()
    media_id = media.id
    if app.config.get("WEBPAGE_SYNC"):  # tests
        process(media_id)
        return

    def run():
        with app.app_context():
            try:
                process(media_id)
            except Exception:
                log.exception("web page %s: check or screenshot failed", media_id)
                db.session.rollback()
            finally:
                db.session.remove()

    threading.Thread(target=run, name=f"webpage-{media_id}", daemon=True).start()


def job() -> str | None:
    """Job: check pages that are due and retake screenshots that are older than their interval."""
    now, done, notes = utcnow(), 0, []
    for media in Media.query.filter(Media.media_type == MEDIA_WEB).all():
        if done >= PER_RUN:
            break
        if needs_work(media, now):
            notes.append(f"{media.id}: {process(media.id)}")
            done += 1
    return "; ".join(notes) or None
