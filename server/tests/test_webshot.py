import http.server
import threading
from pathlib import Path

import pytest
from PIL import Image

from aisignx.extensions import db
from aisignx.media import webshot
from aisignx.models import Media
from aisignx.system import run_checks


def _web_media(app) -> Media:
    with app.app_context():
        return Media.query.filter_by(media_type="web").one()


def _thumb_path(app, media) -> Path:
    return Path(app.config["UPLOAD_FOLDER"]) / "thumbs" / media.thumbnail_name


def test_screenshot_used_when_capture_works(app, admin_client, monkeypatch):
    def fake_capture(url, dest, timeout=None):
        Image.new("RGB", (1280, 720), (10, 120, 200)).save(dest, "PNG")
        return True

    monkeypatch.setattr(webshot, "capture", fake_capture)
    admin_client.post("/media/web", data={"name": "Menu", "url": "https://example.com/menu"})
    media = _web_media(app)
    assert media.thumbnail_status == "ok"
    with Image.open(_thumb_path(app, media)) as img:
        assert img.size == (480, 270)
        assert img.getpixel((10, 10))[2] > 150  # blue from the "screenshot"


def test_title_card_when_no_browser(app, admin_client, monkeypatch):
    monkeypatch.setattr(webshot, "browser_path", lambda: None)
    admin_client.post("/media/web", data={"name": "Weather", "url": "https://weather.example"})
    media = _web_media(app)
    assert media.thumbnail_status == "failed"
    assert _thumb_path(app, media).exists()  # title card stands in
    assert b"no screenshot" in admin_client.get("/media/").data


def test_refresh_and_url_change_regenerate(app, admin_client, monkeypatch):
    calls = []

    def fake_capture(url, dest, timeout=None):
        calls.append(url)
        Image.new("RGB", (1280, 720)).save(dest, "PNG")
        return True

    monkeypatch.setattr(webshot, "capture", fake_capture)
    admin_client.post("/media/web", data={"name": "A", "url": "https://a.example"})
    media = _web_media(app)
    first_thumb = media.thumbnail_name

    admin_client.post(f"/media/{media.id}/thumbnail/refresh")
    admin_client.post(f"/media/{media.id}/edit",
                      data={"name": "A", "url": "https://b.example", "default_duration": "10"})
    admin_client.post(f"/media/{media.id}/edit",  # same URL: no new capture
                      data={"name": "A2", "url": "https://b.example", "default_duration": "10"})
    assert calls == ["https://a.example", "https://a.example", "https://b.example"]
    media = _web_media(app)
    assert media.thumbnail_name != first_thumb
    assert not _thumb_path(app, Media(thumbnail_name=first_thumb)).exists()  # old one cleaned up


def test_thumbnail_status_endpoint(app, admin_client):
    with app.app_context():
        m = Media(name="x", media_type="web", url="https://x.example", thumbnail_status="pending")
        db.session.add(m)
        db.session.commit()
        mid = m.id
    assert admin_client.get(f"/media/thumbnail-status?id={mid}&id=999").json == {"pending": [mid]}


def test_find_browser_respects_configured_path(tmp_path):
    fake = tmp_path / "mybrowser.exe"
    fake.write_text("")
    assert webshot.find_browser(str(fake)) == str(fake)
    assert webshot.find_browser(str(tmp_path / "missing.exe")) is None


def test_check_command_and_dashboard_warning(app, admin_client, monkeypatch):
    monkeypatch.setattr(webshot, "browser_path", lambda: None)
    result = app.test_cli_runner().invoke(args=["check"])
    assert result.exit_code == 1
    assert "MISSING" in result.output and "Web browser" in result.output
    assert b"Some programs AiSignX uses are missing" in admin_client.get("/dashboard").data


@pytest.mark.skipif(webshot.find_browser() is None, reason="no Chromium-based browser installed")
def test_real_browser_screenshot(app, tmp_path):
    """End-to-end: screenshot a page served locally by a tiny HTTP server."""
    (tmp_path / "index.html").write_text(
        "<html><body style='margin:0;background:#ff0000'><h1>AiSignX</h1></body></html>")
    handler = lambda *a, **kw: http.server.SimpleHTTPRequestHandler(*a, directory=str(tmp_path), **kw)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    app.config["BROWSER_PATH"] = webshot.find_browser()  # opt in to the real browser
    try:
        dest = tmp_path / "shot.png"
        with app.app_context():
            assert webshot.capture(f"http://127.0.0.1:{server.server_port}/", dest)
        with Image.open(dest) as img:
            assert img.size == webshot.VIEWPORT
            r, g, b = img.convert("RGB").getpixel((img.width - 5, img.height - 5))
            assert r > 200 and g < 60 and b < 60  # the red background was rendered
    finally:
        server.shutdown()


def test_run_checks_shape(app):
    with app.app_context():
        names = [c.name for c in run_checks()]
    assert any("browser" in n.lower() for n in names) and any("ffmpeg" in n for n in names)
