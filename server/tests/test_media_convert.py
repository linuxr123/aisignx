"""Make a TV-friendly copy of a video (media/convert.py)."""
import shutil
import subprocess
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.media import convert
from aisignx.models import Media

from .conftest import login, make_user, upload

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
needs_ffmpeg = pytest.mark.skipif(not HAVE_FFMPEG, reason="ffmpeg not installed")


def test_argv_shrinks_but_never_enlarges_and_caps_the_frame_rate():
    cmd = convert.argv_for("ffmpeg", "in.mov", "out.mp4", {"fps": 60.0}, 3840, 2160)
    text = " ".join(cmd)
    assert "min(1920,iw)" in text and "min(1080,ih)" in text and "force_original_aspect_ratio=decrease" in text
    assert cmd[cmd.index("-r") + 1] == "30" and cmd[cmd.index("-g") + 1] == "60"  # 2 s of frames
    assert "-movflags" in cmd and "+faststart" in cmd and "libx264" in cmd
    assert cmd[cmd.index("-profile:v") + 1] == "high" and "yuv420p" in text
    assert cmd[-1] == "out.mp4" and cmd[cmd.index("-i") + 1] == "in.mov"


def test_argv_keeps_a_normal_frame_rate_and_flips_the_box_for_portrait():
    cmd = convert.argv_for("ffmpeg", "a", "b", {"fps": 25.0}, 2160, 3840)
    assert "-r" not in cmd and cmd[cmd.index("-g") + 1] == "50"
    assert "min(1080,iw)" in " ".join(cmd) and "min(1920,ih)" in " ".join(cmd)
    # Nothing known about the source: still a valid command.
    assert convert.argv_for("ffmpeg", "a", "b", None, None, None)[0] == "ffmpeg"


def test_progress_lines():
    assert convert._seconds("out_time_us=2500000\n") == 2.5
    assert convert._seconds("frame=12") is None and convert._seconds("out_time_us=N/A") is None


@pytest.fixture
def big_clip(tmp_path):
    clip = tmp_path / "Big.mov"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=2560x1440:rate=60",
                    "-f", "lavfi", "-i", "sine=duration=2", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    "-c:a", "aac", "-shortest", str(clip)], check=True, timeout=120)
    return clip


@pytest.fixture
def editor(app):
    app.config["MEDIA_CONVERT_SYNC"] = True
    make_user(app, "admin", "admin")  # an admin must exist, or the setup wizard takes over
    make_user(app, "ed", "editor")
    c = app.test_client()
    login(c, "ed")
    return c


@needs_ffmpeg
def test_conversion_makes_a_copy_and_keeps_the_original(app, editor, big_clip):
    upload(editor, ("Big.mov", big_clip.read_bytes()))
    with app.app_context():
        original = Media.query.one()
        oid, ochecksum = original.id, original.checksum_sha256
    resp = editor.post(f"/media/{oid}/convert", data={})
    assert resp.status_code in (302, 303)
    st = editor.get(f"/media/{oid}/convert.json").get_json()
    assert st["state"] == "done" and st["pct"] == 100
    with app.app_context():
        assert Media.query.count() == 2
        copy = db.session.get(Media, st["result_id"])
        assert copy.name == "Big (1080p)" and copy.id != oid
        assert copy.width <= 1920 and copy.height <= 1080 and copy.width % 2 == 0
        info = copy.video_info
        assert info["codec"] == "h264" and info["profile"] == "High" and info["pix_fmt"] == "yuv420p"
        assert info["fps"] <= 30.5 and copy.thumbnail_name
        assert copy.playback_warnings == []
        assert db.session.get(Media, oid).checksum_sha256 == ochecksum  # untouched
        folder = Path(app.config["UPLOAD_FOLDER"]) / "media"
        assert (folder / copy.stored_name).is_file() and not list((Path(app.config["UPLOAD_FOLDER"]) / "tmp").glob("convert-*"))


@needs_ffmpeg
def test_conversion_can_replace_the_original_in_place(app, editor, big_clip):
    upload(editor, ("Big.mov", big_clip.read_bytes()))
    with app.app_context():
        original = Media.query.one()
        oid, old_checksum, old_name = original.id, original.checksum_sha256, original.stored_name
    editor.post(f"/media/{oid}/convert", data={"replace": "y"})
    with app.app_context():
        assert Media.query.count() == 1
        media = db.session.get(Media, oid)
        assert media.checksum_sha256 != old_checksum and media.stored_name != old_name
        assert media.height <= 1080 and media.video_info["codec"] == "h264"
        assert not (Path(app.config["UPLOAD_FOLDER"]) / "media" / old_name).exists()


@needs_ffmpeg
def test_a_failed_conversion_reports_and_leaves_the_video_alone(app, editor, big_clip, monkeypatch):
    upload(editor, ("Big.mov", big_clip.read_bytes()))
    with app.app_context():
        oid = Media.query.one().id
    monkeypatch.setattr(convert, "argv_for", lambda *a, **k: ["ffmpeg", "-v", "error", "-i", "missing-file.mov", "x.mp4"])
    editor.post(f"/media/{oid}/convert", data={})
    st = editor.get(f"/media/{oid}/convert.json").get_json()
    assert st["state"] == "failed" and "ffmpeg failed" in st["message"]
    with app.app_context():
        assert Media.query.count() == 1


def test_viewers_and_images_cannot_convert(app, editor):
    make_user(app, "v", "viewer")
    viewer = app.test_client()
    login(viewer, "v")
    assert viewer.post("/media/1/convert", data={}).status_code in (302, 403)
    with app.app_context():
        assert Media.query.count() == 0
