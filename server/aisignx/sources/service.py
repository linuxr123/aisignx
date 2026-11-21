"""Keeping the media library in step with an image source: list it, download what is new or changed, remove what left.

Each image becomes an ordinary Media row (source_id, source_key, source_stamp), so everything that works for uploads works for
these. A sync is bounded (time and number of downloads) and continues at the next run, so a big album fills in over a few minutes
without blocking anything.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import time
from datetime import timedelta

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from ..extensions import db
from ..media import probe, storage
from ..media import service as media_service
from ..models import ImageSource, Media
from ..models.media import MEDIA_IMAGE
from ..timeutil import as_utc, utcnow
from .adapters import adapter_for
from .http import SourceError

log = logging.getLogger(__name__)
HARD_MAX_IMAGES = 1000
RUN_SECONDS = 45          # one sync run stops after this long and carries on at the next run
RUN_DOWNLOADS = 40
RETRY_MIN = 15


def _fernet() -> Fernet:
    raw = str(current_app.config["SECRET_KEY"]).encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"aisignx.image-sources.v1\0" + raw).digest()))


def set_secret(source: ImageSource, value: str | None) -> None:
    value = (value or "").strip()
    source.secret = _fernet().encrypt(value.encode()).decode() if value else None


def get_secret(source: ImageSource) -> str:
    if not source.secret:
        return ""
    try:
        return _fernet().decrypt(source.secret.encode()).decode()
    except (InvalidToken, ValueError):
        log.warning("The saved password of image source %s cannot be decrypted (secret key changed?); enter it again.", source.name)
        return ""


def limit_of(source: ImageSource) -> int:
    return max(1, min(int(source.max_images or 200), HARD_MAX_IMAGES))


def preview(source: ImageSource, count: int = 8) -> list[str]:
    """The names of the first few images the source offers (no download, no import). Raises SourceError."""
    items = adapter_for(source.kind)(source, get_secret(source), min(limit_of(source), 50))
    return [r.name for r in items[:count]] + ([f"… {len(items)} found" ] if len(items) > count else [])


def _import(source: ImageSource, remote, existing: Media | None) -> str:
    """Download one image and create or replace its Media row. -> "new" | "updated"."""
    name = remote.name if "." in remote.name else remote.name + ".jpg"
    ext = storage.extension_of(name)
    stored = storage.new_stored_name(ext)
    path = storage.media_dir() / stored
    try:
        with open(path, "wb") as sink:
            remote.fetch(sink)
        fields = media_service.fields_for_stored(path, name)
    except (SourceError, probe.InvalidMediaError, OSError):
        storage.remove_file(storage.media_dir(), stored)
        raise
    if existing is None:
        media = Media(name=media_service.display_name(name), default_duration=media_service.default_duration(),
                      source_id=source.id, source_key=remote.key[:700], source_stamp=remote.stamp[:160])
        media_service.apply_file(media, fields)
        db.session.add(media)
        db.session.commit()
        return "new"
    old = (existing.stored_name, existing.thumbnail_name)
    media_service.apply_file(existing, fields)
    existing.source_stamp = remote.stamp[:160]
    media_service.commit_replace(existing, old)
    return "updated"


def sync(source: ImageSource, now=None) -> str:
    """One bounded sync run. Updates the source's status line and next time; returns the status text."""
    now = now or utcnow()
    started = time.monotonic()
    new = updated = removed = failed = 0
    try:
        remotes = adapter_for(source.kind)(source, get_secret(source), limit_of(source))
        have = {m.source_key: m for m in Media.query.filter(Media.source_id == source.id, Media.media_type == MEDIA_IMAGE)}
        downloads = 0
        more = False
        for r in remotes:
            m = have.get(r.key)
            if m is not None and m.source_stamp == r.stamp[:160] and m.stored_name:
                continue
            if downloads >= RUN_DOWNLOADS or time.monotonic() - started > RUN_SECONDS:
                more = True
                break
            downloads += 1
            try:
                result = _import(source, r, m)
                new += result == "new"
                updated += result == "updated"
            except (SourceError, probe.InvalidMediaError, OSError) as exc:
                failed += 1
                log.info("image source %s: %s skipped: %s", source.name, r.name, exc)
                db.session.rollback()
        if source.mirror and not more:
            wanted = {r.key for r in remotes}
            for key, m in have.items():
                if key not in wanted:
                    media_service.delete_media(m)
                    removed += 1
        total = Media.query.filter(Media.source_id == source.id).count()
        status = f"{total} images" + "".join(f", {n} {w}" for n, w in ((new, "new"), (updated, "updated"), (removed, "removed"), (failed, "skipped")) if n)
        if more:
            status += " (more to come)"
        ok, wait = True, (1 if more else source.interval_min)
    except SourceError as exc:
        status, ok, wait = str(exc)[:290], False, min(source.interval_min, RETRY_MIN)
    except Exception:  # noqa: BLE001 (a source must never stop the job loop)
        log.exception("image source %s failed", source.name)
        status, ok, wait = "Unexpected error: see Admin > Logs.", False, min(source.interval_min, RETRY_MIN)
    db.session.rollback()
    src = db.session.get(ImageSource, source.id)
    if src is not None:
        src.last_sync_at, src.last_status, src.last_ok = now, status, ok
        src.next_sync_at = now + timedelta(minutes=max(1, wait))
        db.session.commit()
    return status


def delete_source(source: ImageSource) -> int:
    """Remove the source and every image it imported (they are copies). -> how many images were removed."""
    count = 0
    for m in Media.query.filter(Media.source_id == source.id).all():
        media_service.delete_media(m)
        count += 1
    db.session.delete(db.session.get(ImageSource, source.id))
    db.session.commit()
    return count


def job() -> str | None:
    """Every minute: sync each enabled source that is due."""
    now = utcnow()
    done = []
    for source in ImageSource.query.filter(ImageSource.enabled.is_(True)).order_by(ImageSource.id).all():
        if source.next_sync_at is None or as_utc(source.next_sync_at) <= now:
            sync(source, now)
            done.append(source.name)
    return "synced " + ", ".join(done) if done else None
