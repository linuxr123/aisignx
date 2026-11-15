"""Create, replace and delete media items (shared by the admin pages and the JSON API)."""
import logging
import os
import shutil
import time

from ..audit import service as audit
from ..extensions import db
from ..models import Media, Setting
from ..models.media import (DEFAULT_DURATION, MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_WEB, THUMB_FAILED,
                            THUMB_OK)
from . import probe, storage, thumbnails

log = logging.getLogger(__name__)

ORPHAN_MIN_AGE_S = 3600        # unreferenced files in media/ and thumbs/
TMP_MIN_AGE_S = 24 * 3600      # anything left in tmp/
RESTORE_GRACE_S = 24 * 3600    # no orphan deletion this long after a restore


class NotReplaceableError(ValueError):
    """Only uploaded files can be replaced; web pages are edited instead."""


def default_duration() -> int:
    try:
        value = int(Setting.get("default_duration") or DEFAULT_DURATION)
    except (TypeError, ValueError):
        value = DEFAULT_DURATION
    return max(1, value)


def display_name(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0].strip()
    return (base or "Untitled")[:200]


def _store_and_probe(file_storage) -> dict:
    """Save an upload under a new random name and read its metadata.
    Raises probe.InvalidMediaError (and removes the saved file) when it is unusable."""
    filename = file_storage.filename or ""
    media_type = storage.media_type_for(filename)
    if media_type is None:
        raise probe.InvalidMediaError(f"{filename or 'File'}: unsupported file type.")
    stored, size, checksum = storage.save_upload(file_storage, storage.extension_of(filename))
    path = storage.media_dir() / stored
    try:
        info = probe.probe_image(path) if media_type == MEDIA_IMAGE else probe.probe_video(path)
    except probe.InvalidMediaError as exc:
        storage.remove_file(storage.media_dir(), stored)
        raise probe.InvalidMediaError(f"{filename}: {exc}") from exc
    # Videos with the MP4 index at the end can't start until the player fetches the end
    # of the file; the kiosk apps' caches stalled on them. Move the index to the front
    # (lossless, no re-encode) so every player starts at once.
    if media_type != MEDIA_IMAGE and probe.faststart(path):
        size, checksum = storage.size_and_sha256(path)
    return {
        "media_type": media_type,
        "original_filename": filename[:255],
        "stored_name": stored,
        "mime_type": storage.mime_for(filename),
        "file_size": size,
        "checksum_sha256": checksum,
        "width": info.get("width"),
        "height": info.get("height"),
        "duration_seconds": info.get("duration_seconds"),
        "video_info": info.get("video_info"),
        "path": path,
    }


def _apply_file(media: Media, fields: dict) -> None:
    path = fields.pop("path")
    for key, value in fields.items():
        setattr(media, key, value)
    media.thumbnail_name = thumbnails.build_file_thumbnail(media, path)
    media.thumbnail_status = THUMB_OK if media.thumbnail_name else THUMB_FAILED


def create_from_upload(file_storage, user) -> Media:
    """Store one uploaded file as a Media row (no commit). Raises probe.InvalidMediaError."""
    fields = _store_and_probe(file_storage)
    media = Media(name=display_name(fields["original_filename"]),
                  default_duration=default_duration(),
                  uploaded_by_id=getattr(user, "id", None))
    _apply_file(media, fields)
    db.session.add(media)
    return media


def replace_file(media: Media, file_storage) -> tuple[str | None, str | None]:
    """Swap the file behind an uploaded media item, keeping its id, name, default
    duration and playlist items (images and videos may switch type). No commit.
    Returns the old (stored_name, thumbnail_name) for remove_files() after the commit.
    Raises NotReplaceableError for web pages and probe.InvalidMediaError for bad files."""
    if media.media_type == MEDIA_WEB:
        raise NotReplaceableError("Web pages cannot be replaced with a file.")
    old = (media.stored_name, media.thumbnail_name)
    _apply_file(media, _store_and_probe(file_storage))
    return old


def commit_replace(media: Media, old: tuple[str | None, str | None]) -> None:
    """Commit a replace_file() change, then delete the old files. If the commit fails,
    the new files are removed and the error is re-raised."""
    new = (media.stored_name, media.thumbnail_name)
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        remove_files(new)
        raise
    remove_files(old)


def remove_files(names: tuple[str | None, str | None]) -> None:
    """Delete a (stored_name, thumbnail_name) pair; missing or locked files are skipped."""
    stored, thumb = names
    storage.remove_file(storage.media_dir(), stored)
    storage.remove_file(storage.thumbs_dir(), thumb)


def create_web_page(name: str, url: str, duration: int | None, user) -> Media:
    media = Media(
        name=name.strip()[:200],
        media_type=MEDIA_WEB,
        url=url.strip(),
        default_duration=duration or default_duration(),
        uploaded_by_id=getattr(user, "id", None),
    )
    db.session.add(media)
    return media


def delete_media(media: Media) -> None:
    """Delete the row (playlist items cascade) and its files. Audited as media.delete."""
    old = (media.stored_name, media.thumbnail_name)
    audit.record("media.delete", media)
    db.session.delete(media)
    db.session.commit()
    remove_files(old)


# --- housekeeping -----------------------------------------------------------------------

def _old_enough(path, min_age_s: float, now: float) -> bool:
    try:
        return now - path.stat().st_mtime >= min_age_s
    except OSError:
        return False


def _delete(path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True
    except OSError as exc:  # e.g. locked on Windows; try again next run
        log.warning("could not delete %s: %s", path, exc)
        return False


PROBE_BATCH = 10


def probe_missing_job() -> str | None:
    """Job: read the codec, size, bitrate and frame rate of videos uploaded before those facts
    were recorded (a few per run). A file that cannot be probed gets {} so it is not retried."""
    if not probe.ffmpeg_available():
        return "ffprobe not found"
    rows = (Media.query.filter(Media.media_type == MEDIA_VIDEO, Media.video_info.is_(None),
                               Media.stored_name.isnot(None)).limit(PROBE_BATCH).all())
    done = 0
    for media in rows:
        path = storage.media_dir() / media.stored_name
        info = {}
        try:
            if path.is_file():
                info = probe.probe_video(path).get("video_info") or {}
        except probe.InvalidMediaError:
            info = {}
        media.video_info = info
        done += 1
    db.session.commit()
    return f"probed {done} video(s)" if done else None


def orphan_job() -> str:
    """Job: delete files in media/ and thumbs/ that no Media row references (older than
    1 h, so uploads still being saved are left alone) and anything in tmp/ older than 24 h.
    Media files are left alone for a day after a restore, so an admin can still undo it."""
    now = time.time()
    restored = Setting.get_datetime("backup.last_restore_at")
    held = restored is not None and abs(now - restored.timestamp()) < RESTORE_GRACE_S
    rows = db.session.query(Media.stored_name, Media.thumbnail_name).all()
    referenced = {name for row in rows for name in row if name}
    removed = skipped = 0
    for folder in () if held else (storage.media_dir(), storage.thumbs_dir()):
        for path in folder.iterdir():
            if path.name in referenced or not path.is_file():
                continue
            if _old_enough(path, ORPHAN_MIN_AGE_S, now):
                if _delete(path):
                    removed += 1
                else:
                    skipped += 1
    for path in storage.work_dir().iterdir():
        if _old_enough(path, TMP_MIN_AGE_S, now):
            if _delete(path):
                removed += 1
            else:
                skipped += 1
    return (f"removed {removed} file(s)" + (f", {skipped} locked" if skipped else "")
            + ("; media kept (restore in the last 24 h)" if held else ""))
