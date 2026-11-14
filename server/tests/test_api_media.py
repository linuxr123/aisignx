"""JSON media API (including the PowerPoint add-in's exact calls), media replace and the
orphan-file job."""
import os
import secrets
import shutil
import subprocess
import time
from io import BytesIO
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.models import AuditLog, Media, Playlist, PlaylistItem

from .conftest import bearer, make_token, make_user, png_bytes, run_job, upload

HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))
LEGACY, V1 = "/api/media", "/api/v1/media"


@pytest.fixture
def editor_token(app, configured):
    uid = make_user(app, "ed", "editor")
    return make_token(app, uid)


@pytest.fixture(scope="module")
def mp4_bytes(tmp_path_factory) -> bytes:
    if not HAVE_FFMPEG:
        pytest.skip("ffmpeg not installed")
    clip = tmp_path_factory.mktemp("clip") / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi",
                    "-i", "testsrc=duration=2:size=160x90:rate=10", "-pix_fmt", "yuv420p",
                    str(clip)], check=True, timeout=60)
    return clip.read_bytes()


def _uploads(app) -> Path:
    return Path(app.config["UPLOAD_FOLDER"])


def _post(client, path, token, filename, content, mimetype=None, **fields):
    data = dict(fields)
    data["file"] = (BytesIO(content), filename, mimetype) if mimetype else (BytesIO(content),
                                                                           filename)
    return client.post(path, data=data, content_type="multipart/form-data",
                       headers=bearer(token))


def _media(app, media_id) -> Media:
    with app.app_context():
        media = db.session.get(Media, media_id)
        db.session.expunge(media)
        return media


def _addin_name(ext="mp4") -> str:
    return f"ppt_{secrets.token_hex(16)}.{ext}"


# --- upload -----------------------------------------------------------------------------

@pytest.mark.parametrize("path", [LEGACY, V1])
def test_addin_upload_mp4(app, client, editor_token, mp4_bytes, path):
    resp = _post(client, path, editor_token, _addin_name(), mp4_bytes, "video/mp4",
                 name="Quarterly results", duration="15")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    body = resp.get_json()
    assert resp.headers["Location"] == f"/api/v1/media/{body['id']}"
    assert body["name"] == "Quarterly results" and body["type"] == "video"
    assert body["default_duration"] == 15 and body["mime"] == "video/mp4"
    assert body["media_duration_s"] == pytest.approx(2, abs=0.3)
    assert body["duration"] == 2  # the probed length wins for video
    media = _media(app, body["id"])
    assert media.name == "Quarterly results" and media.default_duration == 15


@pytest.mark.parametrize("path", [LEGACY, V1])
def test_addin_style_upload_png(app, client, editor_token, path):
    resp = _post(client, path, editor_token, _addin_name("png"), png_bytes(), "image/png",
                 name="  Slide 1  ", duration="15")
    assert resp.status_code == 201
    body = resp.get_json()
    assert set(body) >= {"id", "name", "type", "mime", "size", "width", "height", "duration",
                         "media_duration_s", "default_duration", "checksum_sha256",
                         "created_at"}
    assert (body["name"], body["type"], body["default_duration"], body["duration"]) == \
        ("Slide 1", "image", 15, 15)
    assert (body["width"], body["height"]) == (64, 36)
    assert body["created_at"].endswith("Z")


def test_upload_without_name_or_duration_uses_defaults(app, client, editor_token):
    resp = _post(client, V1, editor_token, "Lobby poster.png", png_bytes(), name="  ",
                 duration="")
    body = resp.get_json()
    assert resp.status_code == 201
    assert body["name"] == "Lobby poster" and body["default_duration"] == 10


def test_upload_name_is_cut_to_200(app, client, editor_token):
    body = _post(client, V1, editor_token, "a.png", png_bytes(), name="x" * 300).get_json()
    assert body["name"] == "x" * 200


def test_upload_errors(app, client, editor_token):
    resp = client.post(V1, data={"name": "x"}, content_type="multipart/form-data",
                       headers=bearer(editor_token))
    assert resp.status_code == 400 and resp.get_json()["error"] == "missing_file"
    resp = _post(client, V1, editor_token, "notes.txt", b"hello")
    assert resp.status_code == 415
    assert resp.get_json() == {"error": "unsupported_type",
                               "message": "Only image and video files can be uploaded."}
    for bad in ("0", "86401", "ten", "1.5"):
        resp = _post(client, V1, editor_token, "a.png", png_bytes(), duration=bad)
        assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_duration", bad
    resp = _post(client, V1, editor_token, "fake.png", b"not an image")
    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_media"
    with app.app_context():
        assert Media.query.count() == 0
    assert not any((_uploads(app) / "media").iterdir())


def test_oversize_upload_gets_json_413(app, client, editor_token):
    app.config["MAX_CONTENT_LENGTH"] = 1024
    resp = _post(client, LEGACY, editor_token, "big.png", png_bytes(size=(400, 400)) + b"0" * 4096)
    assert resp.status_code == 413
    assert resp.is_json and resp.get_json()["error"] == "too_large"


def test_expect_100_continue_is_accepted(app, client, editor_token):
    data = {"file": (BytesIO(png_bytes()), "a.png"), "name": "Test", "duration": "15"}
    headers = dict(bearer(editor_token), Expect="100-continue")
    resp = client.post(LEGACY, data=data, content_type="multipart/form-data", headers=headers)
    assert resp.status_code == 201


# --- read -------------------------------------------------------------------------------

def test_list_and_get(app, client, editor_token):
    ids = [_post(client, V1, editor_token, f"m{i}.png", png_bytes()).get_json()["id"]
           for i in range(3)]
    resp = client.get(f"{LEGACY}?page=1&per_page=2", headers=bearer(editor_token))
    body = resp.get_json()
    assert (body["page"], body["per_page"], body["total"]) == (1, 2, 3)
    assert [m["id"] for m in body["items"]] == [ids[2], ids[1]]  # newest first
    body = client.get(f"{V1}?page=2&per_page=2", headers=bearer(editor_token)).get_json()
    assert [m["id"] for m in body["items"]] == [ids[0]]
    assert client.get(f"{V1}?per_page=500", headers=bearer(editor_token)).get_json()[
        "per_page"] == 100
    one = client.get(f"{V1}/{ids[1]}", headers=bearer(editor_token))
    assert one.status_code == 200 and one.get_json()["name"] == "m1"
    missing = client.get(f"{V1}/999", headers=bearer(editor_token))
    assert missing.status_code == 404
    assert missing.get_json() == {"error": "not_found", "message": "Media 999 not found."}


def test_viewer_token_can_read(app, client, configured):
    uid = make_user(app, "vic", "viewer")
    raw = make_token(app, uid, scopes="media:read")
    assert client.get(V1, headers=bearer(raw)).status_code == 200


# --- replace ----------------------------------------------------------------------------

def _make_playlist(app, media_id) -> int:
    with app.app_context():
        playlist = Playlist(name="P")
        playlist.items.append(PlaylistItem(media_id=media_id, position=0, duration=7))
        db.session.add(playlist)
        db.session.commit()
        return playlist.id


@pytest.mark.parametrize("path", [LEGACY, V1])
def test_replace_keeps_identity_and_swaps_file(app, client, editor_token, paired_display, path):
    _display_id, display_token = paired_display
    body = _post(client, V1, editor_token, "a.png", png_bytes(), name="Menu",
                 duration="25").get_json()
    media_id = body["id"]
    _make_playlist(app, media_id)
    before = _media(app, media_id)
    old_url = f"/api/player/v1/media/{media_id}/{before.stored_name}"
    assert client.get(old_url, headers=bearer(display_token)).status_code == 200

    resp = _post(client, f"{path}/{media_id}/replace", editor_token, _addin_name("png"),
                 png_bytes(size=(80, 45), color=(0, 0, 255)), "image/png")
    assert resp.status_code == 200, resp.get_data(as_text=True)
    after = _media(app, media_id)
    assert resp.get_json()["id"] == media_id
    assert (after.name, after.default_duration) == ("Menu", 25)
    assert (after.width, after.height) == (80, 45)
    assert after.stored_name != before.stored_name
    assert after.checksum_sha256 != before.checksum_sha256
    assert not (_uploads(app) / "media" / before.stored_name).exists()
    assert not (_uploads(app) / "thumbs" / before.thumbnail_name).exists()
    assert (_uploads(app) / "media" / after.stored_name).exists()
    with app.app_context():
        item = PlaylistItem.query.one()
        assert (item.media_id, item.duration) == (media_id, 7)
        row = AuditLog.query.filter_by(action="media.replace").one()
        assert row.target_id == media_id and before.checksum_sha256 in row.detail
    assert client.get(old_url, headers=bearer(display_token)).status_code == 404
    new_url = f"/api/player/v1/media/{media_id}/{after.stored_name}"
    assert client.get(new_url, headers=bearer(display_token)).status_code == 200


def test_replace_applies_name_and_duration_when_given(app, client, editor_token):
    media_id = _post(client, V1, editor_token, "a.png", png_bytes()).get_json()["id"]
    resp = _post(client, f"{V1}/{media_id}/replace", editor_token, "b.png", png_bytes(),
                 name="New name", duration="42")
    assert (resp.get_json()["name"], resp.get_json()["default_duration"]) == ("New name", 42)


def test_replace_image_with_video(app, client, editor_token, mp4_bytes):
    media_id = _post(client, V1, editor_token, "a.png", png_bytes()).get_json()["id"]
    resp = _post(client, f"{LEGACY}/{media_id}/replace", editor_token, _addin_name(),
                 mp4_bytes, "video/mp4")
    body = resp.get_json()
    assert resp.status_code == 200
    assert (body["type"], body["mime"], body["duration"]) == ("video", "video/mp4", 2)


def test_replace_errors(app, client, editor_token):
    with app.app_context():
        web = Media(name="Site", media_type="web", url="https://example.org")
        db.session.add(web)
        db.session.commit()
        web_id = web.id
    media_id = _post(client, V1, editor_token, "a.png", png_bytes()).get_json()["id"]
    stored = _media(app, media_id).stored_name

    resp = _post(client, f"{V1}/{web_id}/replace", editor_token, "a.png", png_bytes())
    assert resp.status_code == 409 and resp.get_json()["error"] == "not_replaceable"
    resp = _post(client, f"{V1}/999/replace", editor_token, "a.png", png_bytes())
    assert resp.status_code == 404
    assert resp.get_json() == {"error": "not_found", "message": "Media 999 not found."}
    resp = _post(client, f"{V1}/{media_id}/replace", editor_token, "a.txt", b"x")
    assert resp.status_code == 415
    resp = client.post(f"{V1}/{media_id}/replace", data={}, content_type="multipart/form-data",
                       headers=bearer(editor_token))
    assert resp.status_code == 400 and resp.get_json()["error"] == "missing_file"
    resp = _post(client, f"{V1}/{media_id}/replace", editor_token, "bad.png", b"junk")
    assert resp.status_code == 400 and resp.get_json()["error"] == "invalid_media"
    assert _media(app, media_id).stored_name == stored
    assert sorted(p.name for p in (_uploads(app) / "media").iterdir()) == [stored]


# --- admin pages ------------------------------------------------------------------------

def test_admin_replace_form(app, admin_client):
    upload(admin_client, ("a.png", png_bytes()))
    with app.app_context():
        media = Media.query.one()
        media_id, old_name = media.id, media.stored_name
    page = admin_client.get(f"/media/{media_id}/edit").get_data(as_text=True)
    assert f"ID {media_id}" in page and "Replace file" in page
    assert f"/admin/audit/?target=media:{media_id}" in page
    assert f"ID {media_id}" in admin_client.get("/media/").get_data(as_text=True)

    data = {"file": (BytesIO(png_bytes(size=(20, 20))), "new.png")}
    resp = admin_client.post(f"/media/{media_id}/replace", data=data,
                             content_type="multipart/form-data")
    assert resp.status_code == 302
    after = _media(app, media_id)
    assert after.stored_name != old_name and (after.width, after.height) == (20, 20)
    assert not (_uploads(app) / "media" / old_name).exists()

    data = {"file": (BytesIO(b"x"), "bad.txt")}
    resp = admin_client.post(f"/media/{media_id}/replace", data=data,
                             content_type="multipart/form-data", follow_redirects=True)
    assert "unsupported file type" in resp.get_data(as_text=True)
    assert _media(app, media_id).stored_name == after.stored_name


def test_editor_sees_no_history_link_and_viewer_cannot_replace(app, client, configured):
    make_user(app, "ed", "editor")
    make_user(app, "vic", "viewer")
    from .conftest import login

    login(client, "ed")
    upload(client, ("a.png", png_bytes()))
    with app.app_context():
        media_id = Media.query.one().id
    page = client.get(f"/media/{media_id}/edit").get_data(as_text=True)
    assert "Replace file" in page and "/admin/audit/" not in page
    client.post("/logout")
    login(client, "vic")
    data = {"file": (BytesIO(png_bytes()), "b.png")}
    assert client.post(f"/media/{media_id}/replace", data=data,
                       content_type="multipart/form-data").status_code == 403


def test_media_admin_actions_are_audited(app, admin_client):
    upload(admin_client, ("a.png", png_bytes()))
    admin_client.post("/media/web", data={"name": "Site", "url": "https://example.org"})
    with app.app_context():
        image = Media.query.filter_by(media_type="image").one()
        image_id = image.id
    admin_client.post(f"/media/{image_id}/edit", data={"name": "Renamed",
                                                       "default_duration": "12"})
    admin_client.post(f"/media/{image_id}/delete")
    with app.app_context():
        rows = {r.action: r for r in AuditLog.query.all()}
        assert {"media.upload", "media.create_web", "media.update", "media.delete"} <= set(rows)
        assert rows["media.update"].target_id == image_id
        assert '"name":["a","Renamed"]' in rows["media.update"].detail
        assert rows["media.delete"].target_name == "Renamed"
        assert all(r.via == "web" and r.username == "admin" for r in rows.values())


# --- orphan job -------------------------------------------------------------------------

def _touch(path: Path, age_s: float, content=b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(content)
    old = time.time() - age_s
    os.utime(path, (old, old))
    return path


def test_orphan_job_removes_only_old_unreferenced_files(app, admin_client):
    upload(admin_client, ("a.png", png_bytes()))
    media = _media(app, 1)
    root = _uploads(app)
    kept_media = _touch(root / "media" / media.stored_name, 7200)
    kept_thumb = _touch(root / "thumbs" / media.thumbnail_name, 7200)
    old_orphan = _touch(root / "media" / "deadbeef.png", 7200)
    old_thumb = _touch(root / "thumbs" / "deadbeef.jpg", 7200)
    new_orphan = _touch(root / "media" / "fresh.png", 60)
    old_tmp = _touch(root / "tmp" / "leftover.bin", 2 * 86400)
    old_tmp_dir = root / "tmp" / "shot-old"
    _touch(old_tmp_dir / "page.png", 2 * 86400)
    _touch(old_tmp_dir, 2 * 86400)
    new_tmp = _touch(root / "tmp" / "running.bin", 3600)
    screenshot = _touch(root / "screenshots" / "1.jpg", 10 * 86400)

    result = run_job(app, "media.orphans")
    assert "removed 4" in result
    for path in (kept_media, kept_thumb, new_orphan, new_tmp, screenshot):
        assert path.exists(), path
    for path in (old_orphan, old_thumb, old_tmp, old_tmp_dir):
        assert not path.exists(), path
