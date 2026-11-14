"""Uploaded MP4s get their index ("moov") moved to the front (Android/Electron stall fix)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from aisignx.media import probe
from aisignx.models import Media

from .conftest import upload

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                                reason="ffmpeg not installed")


def _make_mp4(path: Path, faststart: bool) -> bytes:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
           "testsrc=duration=2:size=320x180:rate=10", "-f", "lavfi", "-i",
           "sine=frequency=440:duration=2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    subprocess.run(cmd + [str(path)], check=True, timeout=60)
    return path.read_bytes()


def test_box_order_detection(app, tmp_path):
    end = tmp_path / "end.mp4"
    front = tmp_path / "front.mp4"
    _make_mp4(end, faststart=False)
    _make_mp4(front, faststart=True)
    with app.app_context():
        assert probe.needs_faststart(end)
        assert not probe.needs_faststart(front)
        assert not probe.needs_faststart(tmp_path / "nope.webm")


def test_upload_moves_index_to_front(app, admin_client, tmp_path):
    data = _make_mp4(tmp_path / "late-index.mp4", faststart=False)
    with app.app_context():
        assert probe.needs_faststart(tmp_path / "late-index.mp4")

    upload(admin_client, ("late-index.mp4", data))
    with app.app_context():
        media = Media.query.one()
        stored = Path(app.config["UPLOAD_FOLDER"]) / "media" / media.stored_name
        assert not probe.needs_faststart(stored)
        assert media.file_size == stored.stat().st_size  # size/checksum describe the new file
        import hashlib
        assert media.checksum_sha256 == hashlib.sha256(stored.read_bytes()).hexdigest()
        assert media.duration_seconds == pytest.approx(2, abs=0.3)


def test_faststart_without_ffmpeg_leaves_file_alone(app, tmp_path, monkeypatch):
    path = tmp_path / "late.mp4"
    original = _make_mp4(path, faststart=False)
    with app.app_context():
        app.config["FFMPEG_PATH"] = "aisignx-no-such-ffmpeg"
        assert probe.faststart(path) is False
    assert path.read_bytes() == original
