"""Create and delete media items (shared by routes and, later, the API)."""
import os

from ..extensions import db
from ..models import Media, Setting
from ..models.media import DEFAULT_DURATION, MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_WEB
from . import probe, storage


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
    media.thumbnail_name = _build_thumbnail(media, path)
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


def _build_thumbnail(media: Media, src) -> str | None:
    name = storage.new_stored_name("jpg")
    dest = storage.thumbs_dir() / name
    if media.media_type == MEDIA_IMAGE:
        ok = probe.make_image_thumbnail(src, dest)
    elif media.media_type == MEDIA_VIDEO:
        ok = probe.make_video_thumbnail(src, dest, media.duration_seconds)
    else:
        ok = False
    return name if ok else None
