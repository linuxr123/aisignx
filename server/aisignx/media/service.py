"""Create and delete media items (shared by routes and, later, the API)."""
import os

from ..extensions import db
from ..models import Media, Setting
from ..models.media import DEFAULT_DURATION, MEDIA_IMAGE, MEDIA_WEB, THUMB_FAILED, THUMB_OK
from . import probe, storage, thumbnails


def default_duration() -> int:
    try:
        value = int(Setting.get("default_duration") or DEFAULT_DURATION)
    except (TypeError, ValueError):
        value = DEFAULT_DURATION
    return max(1, value)


def display_name(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0].strip()
    return (base or "Untitled")[:200]


def create_from_upload(file_storage, user) -> Media:
    """Store one uploaded file as a Media row. Raises probe.InvalidMediaError."""
    filename = file_storage.filename or ""
    media_type = storage.media_type_for(filename)
    if media_type is None:
        raise probe.InvalidMediaError(f"{filename or 'File'}: unsupported file type.")

    ext = storage.extension_of(filename)
    stored, size, checksum = storage.save_upload(file_storage, ext)
    path = storage.media_dir() / stored
    try:
        info = probe.probe_image(path) if media_type == MEDIA_IMAGE else probe.probe_video(path)
    except probe.InvalidMediaError as exc:
        storage.remove_file(storage.media_dir(), stored)
        raise probe.InvalidMediaError(f"{filename}: {exc}") from exc

    media = Media(
        name=display_name(filename),
        media_type=media_type,
        original_filename=filename[:255],
        stored_name=stored,
        mime_type=storage.mime_for(filename),
        file_size=size,
        checksum_sha256=checksum,
        width=info.get("width"),
        height=info.get("height"),
        duration_seconds=info.get("duration_seconds"),
        default_duration=default_duration(),
        uploaded_by_id=getattr(user, "id", None),
    )
    media.thumbnail_name = thumbnails.build_file_thumbnail(media, path)
    media.thumbnail_status = THUMB_OK if media.thumbnail_name else THUMB_FAILED
    db.session.add(media)
    return media


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
    """Delete the row (playlist items cascade) and its files."""
    stored, thumb = media.stored_name, media.thumbnail_name
    db.session.delete(media)
    db.session.commit()
    storage.remove_file(storage.media_dir(), stored)
    storage.remove_file(storage.thumbs_dir(), thumb)
