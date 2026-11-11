"""Inspect uploaded files and build thumbnails (Pillow for images, ffmpeg for video).

ffmpeg/ffprobe are optional: without them videos still upload, just without a
detected duration or thumbnail.
"""
import json
import shutil
import subprocess
from pathlib import Path

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

THUMB_SIZE = (480, 270)
_TIMEOUT = 60  # seconds per ffmpeg/ffprobe call


class InvalidMediaError(ValueError):
    """The file is not a readable image or video."""


def _tool(config_key: str) -> str | None:
    return shutil.which(current_app.config[config_key])


def ffmpeg_available() -> bool:
    return _tool("FFMPEG_PATH") is not None and _tool("FFPROBE_PATH") is not None


def probe_image(path: Path) -> dict:
    """Return width/height, or raise InvalidMediaError if it isn't an image."""
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidMediaError("File is not a valid image.") from exc
    return {"width": width, "height": height}


def probe_video(path: Path) -> dict:
    """Return width/height/duration_seconds via ffprobe; empty dict if unavailable.

    Raises InvalidMediaError when ffprobe runs but finds no video stream.
    """
    ffprobe = _tool("FFPROBE_PATH")
    if not ffprobe:
        return {}
    cmd = [ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams",
           str(path)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT, check=False)
        data = json.loads(result.stdout or b"{}")
    except (subprocess.TimeoutExpired, OSError, ValueError) as exc:
        current_app.logger.warning("ffprobe failed for %s: %s", path.name, exc)
        return {}

    video = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise InvalidMediaError("File has no video stream.")
    duration = _to_float(video.get("duration")) or _to_float(data.get("format", {}).get("duration"))
    return {
        "width": _to_int(video.get("width")),
        "height": _to_int(video.get("height")),
        "duration_seconds": duration if duration and duration > 0 else None,
    }


def make_image_thumbnail(src: Path, dest: Path) -> bool:
    try:
        with Image.open(src) as img:
            img = ImageOps.exif_transpose(img)
            img.thumbnail(THUMB_SIZE)
            img.convert("RGB").save(dest, "JPEG", quality=82)
        return True
    except (OSError, ValueError) as exc:
        current_app.logger.warning("thumbnail failed for %s: %s", src.name, exc)
        return False


def make_video_thumbnail(src: Path, dest: Path, duration: float | None) -> bool:
    ffmpeg = _tool("FFMPEG_PATH")
    if not ffmpeg:
        return False
    offset = min(1.0, duration / 2) if duration else 0.0
    w, h = THUMB_SIZE
    cmd = [ffmpeg, "-y", "-v", "error", "-ss", f"{offset:.2f}", "-i", str(src),
           "-frames:v", "1", "-vf", f"scale={w}:{h}:force_original_aspect_ratio=decrease",
           str(dest)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT, check=False)
    except (subprocess.TimeoutExpired, OSError) as exc:
        current_app.logger.warning("video thumbnail failed for %s: %s", src.name, exc)
        return False
    return dest.exists() and dest.stat().st_size > 0


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
