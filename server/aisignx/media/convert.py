"""Make a TV-friendly copy of a video with ffmpeg.

Big or unusual videos (4K, very high bitrate, HEVC, 10-bit) can stutter on TV boxes and small
players. This re-encodes a video to something every player decodes smoothly: H.264 High,
8-bit 4:2:0, at most Full HD, at most 30 fps, about 8 Mbps, a keyframe every 2 seconds (so
fast forward and rewind are quick), the index at the front of the file. The original is kept
unless the editor chooses to replace it.

One conversion runs at a time (a second one waits its turn). Progress is kept in memory only:
a restart ends a running conversion and leaves nothing behind but a temp file that the
media.orphans job removes.

build_argv() is pure so it can be tested without ffmpeg.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
from pathlib import Path

from flask import current_app

from ..audit import service as audit
from ..extensions import db
from ..models import Media
from ..models.media import MEDIA_VIDEO
from . import probe, storage

log = logging.getLogger(__name__)

LONG_SIDE, SHORT_SIDE = 1920, 1080
MAX_FPS = 30
VIDEO_BITRATE, MAX_RATE, BUFFER = "8M", "10M", "20M"
AUDIO_BITRATE = "128k"
KEYFRAME_SECONDS = 2
NAME_SUFFIX = " (1080p)"

_lock = threading.Lock()
_gate = threading.Semaphore(1)          # one ffmpeg at a time
_state: dict[int, dict] = {}            # media id -> {state, pct, message, result_id}


def argv_for(ffmpeg: str, src: str, dst: str, facts: dict | None, width: int | None,
             height: int | None) -> list[str]:
    """The ffmpeg command line. facts is the media's video_info (frame rate), width and height
    its size; any of them may be missing."""
    portrait = bool(width and height and height > width)
    max_w, max_h = (SHORT_SIDE, LONG_SIDE) if portrait else (LONG_SIDE, SHORT_SIDE)
    # Shrink to fit inside the box, never enlarge, keep the shape, even sizes for H.264.
    scale = (f"scale=w='min({max_w},iw)':h='min({max_h},ih)'"
             ":force_original_aspect_ratio=decrease:force_divisible_by=2")
    fps = (facts or {}).get("fps")
    out_fps = MAX_FPS if isinstance(fps, (int, float)) and fps > MAX_FPS + 0.5 else (fps or 25)
    gop = max(1, round(float(out_fps) * KEYFRAME_SECONDS))
    cmd = [ffmpeg, "-y", "-v", "error", "-nostdin", "-i", src,
           "-map", "0:v:0", "-map", "0:a:0?",
           "-vf", scale + ",format=yuv420p"]
    if isinstance(fps, (int, float)) and fps > MAX_FPS + 0.5:
        cmd += ["-r", str(MAX_FPS)]
    cmd += ["-c:v", "libx264", "-profile:v", "high", "-level:v", "4.1", "-preset", "veryfast",
            "-b:v", VIDEO_BITRATE, "-maxrate", MAX_RATE, "-bufsize", BUFFER,
            "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ac", "2",
            "-movflags", "+faststart", "-progress", "pipe:1", "-nostats", dst]
    return cmd


def status(media_id: int) -> dict:
    with _lock:
        return dict(_state.get(media_id) or {"state": "idle", "pct": 0, "message": "",
                                              "result_id": None})


def _set(media_id: int, **values) -> None:
    with _lock:
        _state.setdefault(media_id, {"state": "idle", "pct": 0, "message": "", "result_id": None})
        _state[media_id].update(values)


def busy(media_id: int) -> bool:
    return status(media_id)["state"] in ("queued", "running")


def available() -> bool:
    return probe.ffmpeg_available()


def start(media: Media, user, replace: bool) -> bool:
    """Begin converting `media` in the background. False when it is already being converted.
    The caller has checked that it is an uploaded video and that ffmpeg exists."""
    if busy(media.id):
        return False
    _set(media.id, state="queued", pct=0, message="Waiting for another conversion to finish",
         result_id=None)
    app = current_app._get_current_object()
    job = (app, media.id, getattr(user, "id", None), bool(replace))
    if app.config.get("MEDIA_CONVERT_SYNC"):  # tests
        _run(*job)
    else:
        threading.Thread(target=_run, args=job, name=f"convert-{media.id}", daemon=True).start()
    return True


def _seconds(line: str) -> float | None:
    """Seconds encoded so far from an ffmpeg -progress line ("out_time_us=1234567")."""
    key, _, value = line.strip().partition("=")
    if key in ("out_time_us", "out_time_ms"):  # both are microseconds in practice
        try:
            return int(value) / 1_000_000
        except ValueError:
            return None
    return None


def _run(app, media_id: int, user_id: int | None, replace: bool) -> None:
    with _gate, app.app_context():
        tmp: Path | None = None
        try:
            media = db.session.get(Media, media_id)
            if media is None or media.media_type != MEDIA_VIDEO or not media.stored_name:
                raise RuntimeError("The video no longer exists.")
            ffmpeg = shutil.which(app.config["FFMPEG_PATH"])
            if not ffmpeg:
                raise RuntimeError("ffmpeg was not found.")
            src = storage.media_dir() / media.stored_name
            tmp = storage.work_dir() / f"convert-{media.id}-{storage.new_stored_name('mp4')}"
            argv = argv_for(ffmpeg, str(src), str(tmp), media.video_info, media.width, media.height)
            _set(media_id, state="running", pct=0, message="Converting")
            _encode(argv, media.duration_seconds, media_id)
            if not tmp.is_file() or tmp.stat().st_size == 0:
                raise RuntimeError("ffmpeg produced no file.")
            result = _finish(media, tmp, user_id, replace)
            _set(media_id, state="done", pct=100, result_id=result.id,
                 message="Replaced the original." if replace else "Created " + result.name)
        except Exception as exc:  # shown to the editor; the log has the details
            log.exception("converting media %s failed", media_id)
            db.session.rollback()
            _set(media_id, state="failed", message=str(exc)[:300] or "Conversion failed.")
        finally:
            if tmp is not None:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError:
                    pass
            db.session.remove()


def _encode(argv: list[str], duration: float | None, media_id: int) -> None:
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        for line in proc.stdout:  # type: ignore[union-attr]
            done = _seconds(line)
            if done is not None and duration:
                _set(media_id, pct=max(0, min(99, int(done * 100 / duration))))
        err = proc.stderr.read() if proc.stderr else ""  # type: ignore[union-attr]
        code = proc.wait()
    except BaseException:
        proc.kill()
        raise
    if code != 0:
        raise RuntimeError("ffmpeg failed: " + (err or "").strip()[-200:])


def _finish(media: Media, tmp: Path, user_id: int | None, replace: bool) -> Media:
    from . import service  # late: service imports this module's neighbours

    stored = storage.new_stored_name("mp4")
    final = storage.media_dir() / stored
    shutil.move(str(tmp), final)
    base = (media.original_filename or media.name).rsplit(".", 1)[0] or media.name
    fields = service.fields_for_stored(final, base[:250] + ".mp4")
    if replace:
        old = (media.stored_name, media.thumbnail_name)
        service.apply_file(media, fields)
        audit.record("media.convert", media, detail={"replaced": True})
        service.commit_replace(media, old)
        return media
    copy = Media(name=(media.name[:200 - len(NAME_SUFFIX)] + NAME_SUFFIX),
                 default_duration=media.default_duration, uploaded_by_id=user_id)
    service.apply_file(copy, fields)
    db.session.add(copy)
    db.session.flush()
    audit.record("media.convert", copy, detail={"from": media.id, "replaced": False})
    db.session.commit()
    return copy
