"""Video facts and "may not play smoothly" warnings (media/compat.py, probe facts, job)."""
import shutil
import subprocess

import pytest

from aisignx.extensions import db
from aisignx.media import compat, probe
from aisignx.models import Media

from .conftest import run_job, upload

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
SHIELD_FILE = {"codec": "h264", "profile": "High", "level": 51, "pix_fmt": "yuv420p",
               "fps": 25.0, "bitrate": 40_788_463}


def test_codec_strings():
    assert compat.codec_string(SHIELD_FILE) == "avc1.640033"
    assert compat.codec_string({"codec": "h264", "profile": "Main", "level": 40}) == "avc1.4d0028"
    assert compat.codec_string({"codec": "hevc", "profile": "Main 10", "level": 153}) == "hvc1.2.6.L153.B0"
    assert compat.codec_string({"codec": "h264", "profile": "Weird", "level": 40}) is None
    assert compat.codec_string({"codec": "mpeg2video"}) is None and compat.codec_string(None) is None


def test_warnings_for_a_4k_40mbps_file():
    out = compat.warnings(SHIELD_FILE, 3840, 2160)
    assert len(out) == 2 and "3840×2160" in out[0] and "41 Mbps" in out[1]


def test_nothing_to_warn_about_for_a_normal_1080p_file():
    ok = {"codec": "h264", "profile": "High", "level": 41, "pix_fmt": "yuv420p", "fps": 30, "bitrate": 8_000_000}
    assert compat.warnings(ok, 1920, 1080) == [] and not compat.is_heavy(ok, 1920, 1080)
    assert compat.warnings(None) == [] and compat.warnings({}) == []


def test_other_reasons_to_warn():
    base = {"codec": "h264", "pix_fmt": "yuv420p", "fps": 30, "bitrate": 5_000_000}
    assert "HEVC" in compat.warnings({**base, "codec": "hevc"}, 1920, 1080)[0]
    assert "frames per second" in compat.warnings({**base, "fps": 120}, 1920, 1080)[0]
    assert "10-bit" in compat.warnings({**base, "pix_fmt": "yuv420p10le"}, 1920, 1080)[0]


def test_video_facts_from_ffprobe_output():
    stream = {"codec_name": "h264", "profile": "High", "level": 51, "pix_fmt": "yuv420p",
              "avg_frame_rate": "25/1", "bit_rate": "40788463"}
    facts = probe.video_facts(stream, {}, 10.0)
    assert facts == SHIELD_FILE | {"fps": 25.0}
    # No stream bitrate: derived from the file size and length. A bad frame rate is just None.
    facts = probe.video_facts({"codec_name": "vp9", "avg_frame_rate": "0/0"}, {"size": "1000000"}, 8.0)
    assert facts["bitrate"] == 1_000_000 and facts["fps"] is None


def test_media_shows_warnings_and_a_summary(app):
    with app.app_context():
        m = Media(name="Big", media_type="video", width=3840, height=2160, video_info=SHIELD_FILE)
        assert len(m.playback_warnings) == 2
        assert m.video_summary == "H264 High · 25 fps · 40.8 Mbps"
        assert Media(name="Pic", media_type="image", video_info=SHIELD_FILE).playback_warnings == []
        assert Media(name="Old", media_type="video").playback_warnings == []


@pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")
def test_upload_records_facts_and_the_job_fills_in_old_videos(app, admin_client, tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x180:rate=10",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", str(clip)], check=True, timeout=60)
    upload(admin_client, ("Clip.mp4", clip.read_bytes()))
    with app.app_context():
        media = Media.query.one()
        assert media.video_info["codec"] == "h264" and media.video_info["fps"] == 10.0
        assert media.playback_warnings == []
        media.video_info = None  # as if uploaded before the facts were recorded
        db.session.commit()
    assert "probed 1" in run_job(app, "media.probe")
    with app.app_context():
        assert Media.query.one().video_info["codec"] == "h264"
    assert run_job(app, "media.probe") is None  # nothing left to do
