import shutil
import subprocess
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.filters import duration, filesize
from aisignx.models import Media, Playlist, PlaylistItem

from .conftest import login, make_user, png_bytes, upload


def _uploads(app) -> Path:
    return Path(app.config["UPLOAD_FOLDER"])


def _only_media(app) -> Media:
    with app.app_context():
        items = Media.query.all()
        assert len(items) == 1
        return items[0]


def test_upload_image_creates_media_and_thumbnail(app, admin_client):
    resp = upload(admin_client, ("Lobby Welcome.png", png_bytes((1920, 1080))))
    assert resp.status_code == 302

    media = _only_media(app)
    assert media.name == "Lobby Welcome"
    assert media.media_type == "image"
    assert (media.width, media.height) == (1920, 1080)
    assert media.default_duration == 10
    assert len(media.checksum_sha256) == 64
    assert media.stored_name != "Lobby Welcome.png"  # random name on disk
    assert (_uploads(app) / "media" / media.stored_name).exists()
    assert (_uploads(app) / "thumbs" / media.thumbnail_name).exists()

    assert admin_client.get(f"/media/{media.id}/file").data == png_bytes((1920, 1080))
    assert admin_client.get(f"/media/{media.id}/thumb").mimetype == "image/jpeg"
    assert b"Lobby Welcome" in admin_client.get("/media/").data


def test_upload_multiple_and_uses_default_duration_setting(app, admin_client):
    admin_client.post("/settings/", data={"org_name": "X", "timezone": "UTC", "default_duration": "7"})
    upload(admin_client, ("a.png", png_bytes()), ("b.jpg", png_bytes()))
    with app.app_context():
        assert sorted(m.default_duration for m in Media.query.all()) == [7, 7]


def test_rejects_fake_and_unsupported_files(app, admin_client):
    upload(admin_client, ("evil.png", b"not really a png"), ("script.svg", b"<svg/>"),
           ("notes.txt", b"hello"))
    with app.app_context():
        assert Media.query.count() == 0
    media_dir = _uploads(app) / "media"
    assert not media_dir.exists() or not any(media_dir.iterdir())  # nothing left behind


def test_add_web_page(app, admin_client):
    admin_client.post("/media/web", data={"name": "Menu", "url": "https://example.com/menu", "duration": "30"})
    media = _only_media(app)
    assert media.media_type == "web" and media.url == "https://example.com/menu"
    assert media.effective_duration == 30


@pytest.mark.parametrize("url", ["javascript:alert(1)", "ftp://example.com", "example.com", ""])
def test_web_page_requires_http_url(app, admin_client, url):
    admin_client.post("/media/web", data={"name": "Bad", "url": url})
    with app.app_context():
        assert Media.query.count() == 0


def test_edit_media(app, admin_client):
    upload(admin_client, ("a.png", png_bytes()))
    media = _only_media(app)
    assert admin_client.get(f"/media/{media.id}/edit").status_code == 200
    admin_client.post(f"/media/{media.id}/edit", data={"name": "Renamed", "default_duration": "25"})
    media = _only_media(app)
    assert (media.name, media.default_duration) == ("Renamed", 25)


def test_delete_media_removes_files_and_playlist_items(app, admin_client):
    upload(admin_client, ("a.png", png_bytes()))
    media = _only_media(app)
    with app.app_context():
        playlist = Playlist(name="P")
        playlist.items.append(PlaylistItem(media_id=media.id, position=0))
        db.session.add(playlist)
        db.session.commit()

    admin_client.post(f"/media/{media.id}/delete")
    with app.app_context():
        assert Media.query.count() == 0
        assert PlaylistItem.query.count() == 0  # cascaded
        assert Playlist.query.count() == 1      # playlist itself is kept
    assert not (_uploads(app) / "media" / media.stored_name).exists()
    assert not (_uploads(app) / "thumbs" / media.thumbnail_name).exists()


def test_viewer_can_browse_but_not_change(app, client, configured):
    make_user(app, "val", "viewer")
    login(client, "val")
    assert client.get("/media/").status_code == 200
    assert upload(client, ("a.png", png_bytes())).status_code == 403
    assert client.post("/media/web", data={"name": "x", "url": "https://x.org"}).status_code == 403
    with app.app_context():
        assert Media.query.count() == 0


def test_files_require_login(app, admin_client):
    upload(admin_client, ("a.png", png_bytes()))
    media = _only_media(app)
    admin_client.post("/logout")
    assert admin_client.get(f"/media/{media.id}/file").status_code == 302


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")
def test_upload_video_detects_duration_and_thumbnail(app, admin_client, tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x180:rate=10",
                    "-pix_fmt", "yuv420p", str(clip)], check=True, timeout=60)
    upload(admin_client, ("Promo.mp4", clip.read_bytes()))
    media = _only_media(app)
    assert media.media_type == "video"
    assert (media.width, media.height) == (320, 180)
    assert media.duration_seconds == pytest.approx(3, abs=0.2)
    assert media.effective_duration == 3
    assert media.thumbnail_name


def test_filters():
    assert filesize(512) == "512 B"
    assert filesize(1536) == "1.5 KB"
    assert filesize(5 * 1024 ** 3) == "5.0 GB"
    assert duration(9) == "0:09"
    assert duration(125) == "2:05"
    assert duration(3725) == "1:02:05"
    assert duration(None) == "—"
