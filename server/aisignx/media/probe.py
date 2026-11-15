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


_ISO_EXTS = {".mp4", ".m4v", ".mov"}


def _top_level_boxes(path: Path, limit: int = 64) -> list[str]:
    """Names of the top-level ISO BMFF boxes ("ftyp", "mdat", "moov", ...), in file order."""
    names, size_total = [], path.stat().st_size
    with open(path, "rb") as fh:
        pos = 0
        while pos + 8 <= size_total and len(names) < limit:
            fh.seek(pos)
            header = fh.read(16)
            if len(header) < 8:
                break
            size = int.from_bytes(header[:4], "big")
            name = header[4:8].decode("latin-1")
            if size == 1 and len(header) >= 16:  # 64-bit size follows
                size = int.from_bytes(header[8:16], "big")
            elif size == 0:  # box runs to the end of the file
                size = size_total - pos
            if size < 8:
                break
            names.append(name)
            pos += size
    return names


def needs_faststart(path: Path) -> bool:
    """True for an MP4/MOV whose index ("moov") comes after the media data ("mdat").
    Such files make a player fetch the end of the file before it can start, which the
    kiosk apps' caches and many slow networks handle badly."""
    if path.suffix.lower() not in _ISO_EXTS:
        return False
    try:
        boxes = _top_level_boxes(path)
    except OSError:
        return False
    return "moov" in boxes and "mdat" in boxes and boxes.index("moov") > boxes.index("mdat")


def faststart(path: Path) -> bool:
    """Move the MP4 index to the front without re-encoding (ffmpeg -c copy +faststart).
    Returns True when the file was rewritten. Missing ffmpeg or any failure leaves the
    file untouched."""
    if not needs_faststart(path):
        return False
    ffmpeg = _tool("FFMPEG_PATH")
    if not ffmpeg:
        return False
    tmp = path.with_name(path.stem + ".faststart" + path.suffix)
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(path), "-map", "0", "-c", "copy",
           "-movflags", "+faststart", str(tmp)]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=_TIMEOUT * 5, check=False)
        if result.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0 \
                or needs_faststart(tmp):
            current_app.logger.warning("faststart failed for %s: %s", path.name,
                                       (result.stderr or b"")[-300:])
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(path)
        return True
    except (subprocess.TimeoutExpired, OSError) as exc:
        current_app.logger.warning("faststart failed for %s: %s", path.name, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False


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
        "video_info": video_facts(video, data.get("format") or {}, duration),
    }


def _fps(text) -> float | None:
    try:
        num, _, den = str(text).partition("/")
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return None
    return round(value, 2) if 0 < value < 1000 else None


def video_facts(stream: dict, fmt: dict, duration: float | None) -> dict:
    """The few ffprobe facts that decide whether a player can decode a video smoothly."""
    bitrate = _to_int(stream.get("bit_rate")) or _to_int(fmt.get("bit_rate"))
    if not bitrate and duration and _to_int(fmt.get("size")):
        bitrate = int(_to_int(fmt.get("size")) * 8 / duration)
    return {
        "codec": str(stream.get("codec_name") or "")[:20] or None,
        "profile": str(stream.get("profile") or "")[:30] or None,
        "level": _to_int(stream.get("level")),
        "pix_fmt": str(stream.get("pix_fmt") or "")[:20] or None,
        "fps": _fps(stream.get("avg_frame_rate")) or _fps(stream.get("r_frame_rate")),
        "bitrate": bitrate,
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
