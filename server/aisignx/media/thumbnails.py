"""Thumbnail generation, including background screenshots of web pages.

Web screenshots take a few seconds, so they run on a small background worker.
If the browser is missing or the page can't be captured, a title card is drawn instead.
"""
import tempfile
import textwrap
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, current_app
from PIL import Image, ImageDraw, ImageFont

from ..extensions import db
from ..models import Media
from ..models.media import (MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_WEB, THUMB_FAILED, THUMB_OK,
                            THUMB_PENDING)
from . import probe, storage, webshot

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="aisignx-thumbs")


def build_file_thumbnail(media: Media, src: Path) -> str | None:
    """Create a thumbnail for an uploaded image/video. Returns the stored thumb name."""
    name = storage.new_stored_name("jpg")
    dest = storage.thumbs_dir() / name
    if media.media_type == MEDIA_IMAGE:
        ok = probe.make_image_thumbnail(src, dest)
    elif media.media_type == MEDIA_VIDEO:
        ok = probe.make_video_thumbnail(src, dest, media.duration_seconds)
    else:
        ok = False
    return name if ok else None


def queue_thumbnail(media: Media) -> None:
    """Schedule (re)generation of a thumbnail. Call after the media row is committed."""
    media.thumbnail_status = THUMB_PENDING
    db.session.commit()
    app = current_app._get_current_object()
    if app.config.get("THUMBNAILS_ASYNC", True):
        _executor.submit(_run_in_app, app, media.id)
    else:
        regenerate(media.id)


def _run_in_app(app: Flask, media_id: int) -> None:
    with app.app_context():
        try:
            regenerate(media_id)
        except Exception:  # never let a worker crash silently
            app.logger.exception("thumbnail job failed for media %s", media_id)
            db.session.rollback()
            media = db.session.get(Media, media_id)
            if media is not None:
                media.thumbnail_status = THUMB_FAILED
                db.session.commit()


def regenerate(media_id: int) -> None:
    media = db.session.get(Media, media_id)
    if media is None:
        return
    old = media.thumbnail_name
    if media.media_type == MEDIA_WEB:
        name, status = _web_thumbnail(media)
    else:
        name = build_file_thumbnail(media, storage.media_dir() / media.stored_name)
        status = THUMB_OK if name else THUMB_FAILED
    media.thumbnail_name = name or old
    media.thumbnail_status = status
    db.session.commit()
    if name and old and old != name:
        storage.remove_file(storage.thumbs_dir(), old)


def _web_thumbnail(media: Media) -> tuple[str | None, str]:
    name = storage.new_stored_name("jpg")
    dest = storage.thumbs_dir() / name
    with tempfile.TemporaryDirectory(prefix="shot-", dir=storage.work_dir(),
                                     ignore_cleanup_errors=True) as tmp:
        shot = Path(tmp) / "shot.png"
        if webshot.capture(media.url, shot) and probe.make_image_thumbnail(shot, dest):
            return name, THUMB_OK
    # No browser, or the page could not be loaded: draw a title card instead.
    title_card(media.name, urlsplit(media.url or "").netloc, dest)
    return name, THUMB_FAILED


def title_card(title: str, subtitle: str, dest: Path) -> None:
    width, height = probe.THUMB_SIZE
    img = Image.new("RGB", (width, height), (33, 37, 41))
    draw = ImageDraw.Draw(img)
    big, small = _font(30), _font(18)
    lines = textwrap.wrap(title, width=24)[:3] or ["Web page"]
    y = height // 2 - (len(lines) * 38) // 2 - 12
    for line in lines:
        draw.text((width // 2, y), line, font=big, fill=(248, 249, 250), anchor="mt")
        y += 38
    draw.text((width // 2, y + 10), subtitle[:48], font=small, fill=(173, 181, 189), anchor="mt")
    img.save(dest, "JPEG", quality=85)


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 has no scalable default font
        return ImageFont.load_default()
