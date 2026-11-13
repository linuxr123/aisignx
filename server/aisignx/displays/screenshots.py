"""On-demand screenshots of a display.

An editor asks for one; the server stores a one-time request id and sends it to the
screen over SSE (and again when the screen reconnects within REQUEST_TTL_S). The screen
uploads an image with that id; the server re-encodes it to a JPEG at a path it chooses.
A device can therefore never push an image it was not asked for.

Every column write here is a Core UPDATE: an ORM write of a Display would broadcast a
manifest hint to every screen. Nothing here commits; callers do.
"""
import os
import secrets
import struct
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from PIL import Image
from sqlalchemy import update

from ..extensions import db
from ..media import storage
from ..models import Display
from ..timeutil import as_utc, utcnow

REQUEST_TTL_S = 120
RESEND_AFTER_S = 15
MAX_SIDE = 1920
MAX_PIXELS = 40_000_000
JPEG_QUALITY = 80
_FORMATS = {"JPEG", "PNG"}
_DECODE_ERRORS = (OSError, SyntaxError, ValueError, TypeError, EOFError, struct.error,
                  Image.DecompressionBombError)


def path_for(display_id: int) -> Path:
    return storage.screenshots_dir() / f"{int(display_id)}.jpg"


def _age_s(when: datetime | None, now: datetime) -> float | None:
    if when is None:
        return None
    return (as_utc(now) - as_utc(when)).total_seconds()


def pending(display: Display, now: datetime | None = None) -> str | None:
    """The open request id, if one was made less than REQUEST_TTL_S ago."""
    age = _age_s(display.screenshot_requested_at, now or utcnow())
    if not display.screenshot_request or age is None or age >= REQUEST_TTL_S:
        return None
    return display.screenshot_request


def request(display: Display, now: datetime | None = None) -> str | None:
    """Open a new request and return its id; None if one is under RESEND_AFTER_S old."""
    now = now or utcnow()
    age = _age_s(display.screenshot_requested_at, now)
    if display.screenshot_request and age is not None and age < RESEND_AFTER_S:
        return None
    rid = secrets.token_hex(8)
    db.session.execute(
        update(Display).where(Display.id == display.id)
        .values(screenshot_request=rid, screenshot_requested_at=now)
        .execution_options(synchronize_session=False))
    return rid


def process(data: bytes) -> bytes:
    """Validate an uploaded JPEG/PNG and re-encode it (EXIF dropped, longest side
    at most MAX_SIDE). Raises ValueError for anything that is not a sane image."""
    try:
        with Image.open(BytesIO(data)) as probe:
            if probe.format not in _FORMATS:
                raise ValueError("unsupported image format")
            width, height = probe.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise ValueError("image too large")
            probe.verify()
        with Image.open(BytesIO(data)) as img:
            img.load()
            rgb = img.convert("RGB")
        rgb.thumbnail((MAX_SIDE, MAX_SIDE))
        out = BytesIO()
        rgb.save(out, "JPEG", quality=JPEG_QUALITY)
        return out.getvalue()
    except ValueError:
        raise
    except _DECODE_ERRORS as exc:
        raise ValueError(f"bad image: {exc}") from exc


def store(display_id: int, rid: str, jpeg: bytes) -> bool:
    """Claim request rid and write the file. False if rid is no longer the open request.

    The caller commits, or rolls back on OSError (the claim then stays open).
    """
    result = db.session.execute(
        update(Display)
        .where(Display.id == display_id, Display.screenshot_request == rid)
        .values(screenshot_request=None, screenshot_requested_at=None,
                screenshot_at=utcnow())
        .execution_options(synchronize_session=False))
    if result.rowcount != 1:
        return False
    target = path_for(display_id)
    temp = target.with_name(f".{target.stem}.{secrets.token_hex(4)}.tmp")
    try:
        temp.write_bytes(jpeg)
        os.replace(temp, target)
    except OSError:
        try:
            temp.unlink()
        except OSError:
            pass
        raise
    return True


def exists(display_id: int) -> bool:
    return path_for(display_id).is_file()


def remove(display_id: int) -> None:
    """Delete the display's screenshot; nothing happens if there is none."""
    storage.remove_file(storage.screenshots_dir(), path_for(display_id).name)
