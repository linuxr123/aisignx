"""Web pages that refuse embedding: detection, screenshots, manifest, admin (media/webpage.py)."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.media import webpage, webshot
from aisignx.models import Display, Media, Playlist, PlaylistItem

from .conftest import DISPLAY_TOKEN, bearer, login, make_user

API = "/api/player/v1"
PAGE = b"<html><body style='background:#0a7;color:#fff;font-size:80px'>Hello signage</body></html>"
HAVE_BROWSER = webshot.find_browser() is not None


class _Handler(BaseHTTPRequestHandler):
    HEADERS = {
        "/ok": [],
        "/deny": [("X-Frame-Options", "DENY")],
        "/same": [("X-Frame-Options", "SAMEORIGIN")],
        "/none": [("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'")],
        "/open": [("Content-Security-Policy", "frame-ancestors *")],
    }

    def do_GET(self):
        headers = self.HEADERS.get(self.path.split("?")[0])
        code = 200 if headers is not None else 404
        self.send_response(code)
        for name, value in headers or []:
            self.send_header(name, value)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


@pytest.fixture
def site():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def papp(app):
    app.config["WEBPAGE_SYNC"] = True
    return app


def _web(app, url, **fields):
    with app.app_context():
        media = Media(name="Page", media_type="web", url=url, **fields)
        db.session.add(media)
        db.session.commit()
        return media.id


# --- header rules (pure) ------------------------------------------------------------------

@pytest.mark.parametrize("headers, blocked", [
    ([], False),
    ([("X-Frame-Options", "DENY")], True),
    ([("x-frame-options", "sameorigin")], True),
    ([("X-Frame-Options", "ALLOW-FROM https://x.example")], False),  # obsolete: browsers ignore it
    ([("Content-Security-Policy", "frame-ancestors 'none'")], True),
    ([("Content-Security-Policy", "frame-ancestors 'self' https://a.example")], True),
    ([("Content-Security-Policy", "default-src 'self'; frame-ancestors *")], False),
    ([("Content-Security-Policy", "default-src 'self'")], False),
    ([("Content-Security-Policy-Report-Only", "frame-ancestors 'none'")], False),
])
def test_blocks_embedding(headers, blocked):
    assert webpage.blocks_embedding(headers) is blocked


def test_check_embeddable_reads_the_real_headers(site):
    assert webpage.check_embeddable(site + "/ok") is True
    assert webpage.check_embeddable(site + "/open") is True
    assert webpage.check_embeddable(site + "/deny") is False
    assert webpage.check_embeddable(site + "/same") is False
    assert webpage.check_embeddable(site + "/none") is False
    assert webpage.check_embeddable("http://127.0.0.1:9/") is None  # nothing there: unknown


def test_refresh_interval_is_kept_sensible():
    assert webpage.clamp_refresh(10) == 60 and webpage.clamp_refresh(10**9) == 86400
    assert webpage.clamp_refresh("x") == 300 and webpage.clamp_refresh(600) == 600


# --- the decision and the manifest ---------------------------------------------------------

def test_automatic_mode_uses_a_screenshot_only_when_the_site_forbids_embedding(papp):
    with papp.app_context():
        assert Media(media_type="web", web_mode="auto", web_embeddable=False).shows_snapshot
        assert not Media(media_type="web", web_mode="auto", web_embeddable=True).shows_snapshot
        assert not Media(media_type="web", web_mode="auto", web_embeddable=None).shows_snapshot
        assert Media(media_type="web", web_mode="screenshot", web_embeddable=True).shows_snapshot
        assert not Media(media_type="web", web_mode="live", web_embeddable=False).shows_snapshot
        assert not Media(media_type="image", web_mode="screenshot").shows_snapshot


def test_process_checks_and_records_the_answer(papp, site, monkeypatch):
    monkeypatch.setattr(webpage, "refresh_snapshot", lambda media: False)  # no browser needed here
    blocked, fine = _web(papp, site + "/deny"), _web(papp, site + "/ok")
    with papp.app_context():
        assert "checked" in webpage.process(blocked)
        assert webpage.process(blocked).startswith("screenshot failed")  # checked today: only the shot is due
        webpage.process(fine)
        a, b = db.session.get(Media, blocked), db.session.get(Media, fine)
        assert a.web_embeddable is False and a.web_checked_at is not None
        assert b.web_embeddable is True and webpage.process(fine) == ""  # nothing due


def _manifest_items(client):
    return client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).get_json()["items"]


def test_manifest_plays_a_blocked_page_as_its_screenshot(papp, configured, client, paired_display, monkeypatch):
    mid = _web(papp, "https://blocked.example/", web_embeddable=False)
    live = _web(papp, "https://open.example/", web_embeddable=True)
    with papp.app_context():
        pl = Playlist(name="P")
        pl.items.append(PlaylistItem(media_id=mid, position=0))
        pl.items.append(PlaylistItem(media_id=live, position=1))
        db.session.add(pl)
        db.session.flush()
        db.session.get(Display, paired_display[0]).playlist_id = pl.id
        db.session.commit()
    # No screenshot yet: the blocked page is left out (it would only show an error).
    items = _manifest_items(client)
    assert [i["media_id"] for i in items] == [live] and items[0]["type"] == "web"
    with papp.app_context():
        m = db.session.get(Media, mid)
        m.snapshot_name, m.snapshot_sha256, m.snapshot_size = "abc.png", "a" * 64, 1234
        db.session.commit()
        folder = Path(papp.config["UPLOAD_FOLDER"]) / "media"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "abc.png").write_bytes(b"\x89PNG fake")
    items = _manifest_items(client)
    shot = next(i for i in items if i["media_id"] == mid)
    assert shot["type"] == "image" and shot["mime"] == "image/png" and shot["sha256"] == "a" * 64
    assert shot["url"] == f"{API}/media/{mid}/abc.png" and (shot["width"], shot["height"]) == (1920, 1080)
    assert next(i for i in items if i["media_id"] == live)["type"] == "web"
    # The screenshot file is served to a display (and only that file).
    assert client.get(shot["url"], headers=bearer(DISPLAY_TOKEN)).data == b"\x89PNG fake"
    assert client.get(f"{API}/media/{mid}/other.png", headers=bearer(DISPLAY_TOKEN)).status_code == 404
    # Orphan clean-up keeps a referenced screenshot.
    from .conftest import run_job
    run_job(papp, "media.orphans")
    assert (folder / "abc.png").exists()


# --- admin ----------------------------------------------------------------------------------

def test_adding_and_editing_a_web_page(papp, site, monkeypatch):
    monkeypatch.setattr(webpage, "refresh_snapshot", lambda media: False)
    make_user(papp, "admin", "admin")
    c = papp.test_client()
    login(c, "admin")
    r = c.post("/media/web", data={"name": "Wall", "url": site + "/deny", "duration": "15",
                                   "web_mode": "auto", "refresh_min": "2"})
    assert r.status_code == 302
    with papp.app_context():
        m = Media.query.one()
        assert m.web_mode == "auto" and m.web_refresh_s == 120 and m.web_embeddable is False
        mid = m.id
    page = c.get(f"/media/{mid}/edit").get_data(as_text=True)
    assert "does not allow being embedded" in page and "Screenshot" in page
    assert 'name="refresh_min"' in page and 'value="2"' in page
    assert "Screenshot" in c.get("/media/").get_data(as_text=True)
    # Switch to a live page at another address: the old screenshot is dropped and it is checked again.
    c.post(f"/media/{mid}/edit", data={"name": "Wall", "url": site + "/ok", "default_duration": "15",
                                       "web_mode": "live", "refresh_min": "5"})
    with papp.app_context():
        m = db.session.get(Media, mid)
        assert m.web_mode == "live" and not m.shows_snapshot and m.web_refresh_s == 300
        assert m.snapshot_name is None
    assert c.post(f"/media/{mid}/webpage/refresh", data={}).status_code == 302


def test_images_can_still_be_edited(papp):
    """The web fields are absent from an image's edit form and must not block saving it."""
    from .conftest import png_bytes, upload
    make_user(papp, "admin", "admin")
    c = papp.test_client()
    login(c, "admin")
    upload(c, ("a.png", png_bytes()))
    with papp.app_context():
        mid = Media.query.one().id
    r = c.post(f"/media/{mid}/edit", data={"name": "Renamed", "default_duration": "9"})
    assert r.status_code == 302
    with papp.app_context():
        assert db.session.get(Media, mid).name == "Renamed"


@pytest.mark.skipif(not HAVE_BROWSER, reason="needs Edge, Chrome or Chromium")
def test_a_real_screenshot_is_taken_and_refreshed(papp, site):
    papp.config["BROWSER_PATH"] = None  # the test app disables the browser by default
    mid = _web(papp, site + "/deny", web_refresh_s=60)
    with papp.app_context():
        status = webpage.process(mid)
        assert "checked" in status and status.endswith("screenshot")
        m = db.session.get(Media, mid)
        path = Path(papp.config["UPLOAD_FOLDER"]) / "media" / m.snapshot_name
        assert m.shows_snapshot and path.read_bytes()[:4] == b"\x89PNG" and m.snapshot_size == path.stat().st_size
        first = m.snapshot_name
        assert webpage.process(mid) == ""               # not due again yet
        m.snapshot_at = None                            # the interval has passed
        db.session.commit()
        assert webpage.process(mid).endswith("screenshot")
        m = db.session.get(Media, mid)
        # An identical page keeps the same file: screens do not re-download for nothing.
        assert m.snapshot_name in (first,) or not (Path(papp.config["UPLOAD_FOLDER"]) / "media" / first).exists()
