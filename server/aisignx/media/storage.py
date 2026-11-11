"""Where media files live on disk, and how they get there.

Layout under UPLOAD_FOLDER:
    media/<random>.<ext>     original uploads
    thumbs/<random>.jpg      thumbnails
Stored names are random, so user-supplied filenames never touch the filesystem.
"""
import hashlib
import os
import secrets
from pathlib import Path

from flask import current_app

from ..models.media import MEDIA_IMAGE, MEDIA_VIDEO

IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp", "bmp"}
VIDEO_EXTENSIONS = {"mp4", "webm", "mov", "m4v", "mkv"}

_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif",
    "webp": "image/webp", "bmp": "image/bmp",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "m4v": "video/x-m4v", "mkv": "video/x-matroska",
}


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def media_type_for(filename: str) -> str | None:
    ext = extension_of(filename)
    if ext in IMAGE_EXTENSIONS:
        return MEDIA_IMAGE
    if ext in VIDEO_EXTENSIONS:
        return MEDIA_VIDEO
    return None


def mime_for(filename: str) -> str:
    return _MIME.get(extension_of(filename), "application/octet-stream")


def _folder(name: str) -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]) / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def media_dir() -> Path:
    return _folder("media")


def thumbs_dir() -> Path:
    return _folder("thumbs")


def work_dir() -> Path:
    """Scratch space for jobs. Kept under the upload folder rather than /tmp because
    snap-packaged browsers (Ubuntu's Chromium) have a private /tmp."""
    return _folder("tmp")


def new_stored_name(ext: str) -> str:
    return f"{secrets.token_hex(16)}.{ext}"


def save_upload(file_storage, ext: str) -> tuple[str, int, str]:
    """Save an uploaded file. Returns (stored_name, size_bytes, sha256)."""
    stored = new_stored_name(ext)
    path = media_dir() / stored
    file_storage.save(path)
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return stored, path.stat().st_size, digest.hexdigest()


def remove_file(folder: Path, name: str | None) -> None:
    if not name:
        return
    try:
        os.remove(folder / name)
    except FileNotFoundError:
        pass
    except OSError as exc:  # e.g. file locked on Windows; leave it, don't fail the request
        current_app.logger.warning("could not delete %s: %s", name, exc)
