"""Image sources: folders, web folders, WebDAV and the cloud kinds; the sync into the media library; the admin page; the plugin."""
import io
import json
import os
import re
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.models import ImageSource, Media
from aisignx.sources import service
from aisignx.sources.adapters import cloud
from aisignx.sources import http as shttp

from .conftest import login, make_user, png_bytes


def _source(app, kind, location, **kw):
    with app.app_context():
        s = ImageSource(name=kw.pop("name", f"s-{kind}"), kind=kind, location=location, **kw)
        db.session.add(s)
        db.session.commit()
        return s.id


def _sync(app, sid):
    with app.app_context():
        return service.sync(db.session.get(ImageSource, sid))


def _media(app, sid):
    with app.app_context():
        return sorted((m.source_key, m.name) for m in Media.query.filter_by(source_id=sid))


@pytest.fixture
def photos(tmp_path):
    d = tmp_path / "photos"
    (d / "sub").mkdir(parents=True)
    for i, name in enumerate(("a.png", "b.png", "sub/c.png")):
        (d / name).write_bytes(png_bytes(color=(10 * i + 20, 30, 40)))
    (d / "notes.txt").write_text("not an image")
    return d


def test_a_folder_is_imported_updated_and_mirrored(app, photos):
    sid = _source(app, "folder", str(photos))
    assert "3 images, 3 new" in _sync(app, sid)
    assert [k for k, _n in _media(app, sid)] == ["a.png", "b.png", "sub/c.png"]
    assert "3 images" in _sync(app, sid) and "new" not in _sync(app, sid)                    # nothing changed: nothing downloaded
    (photos / "a.png").write_bytes(png_bytes(color=(250, 250, 10), size=(80, 45)))             # changed
    (photos / "b.png").unlink()                                                                  # gone
    status = _sync(app, sid)
    assert "1 updated" in status and "1 removed" in status
    assert [k for k, _n in _media(app, sid)] == ["a.png", "sub/c.png"]
    with app.app_context():
        m = Media.query.filter_by(source_id=sid, source_key="a.png").one()
        assert m.width == 80 and m.stored_name and m.thumbnail_name
        assert (Path(app.config["UPLOAD_FOLDER"]) / "media" / m.stored_name).is_file()
        # the main library is untouched by the source's own files
        assert Media.query.filter(Media.source_id.is_(None)).count() == 0


def test_not_recursive_mirror_off_and_a_limit_of_the_newest(app, photos):
    sid = _source(app, "folder", str(photos), recursive=False, mirror=False, max_images=1)
    for i, name in enumerate(("a.png", "b.png")):
        os.utime(photos / name, (1_000_000 + i * 100, 1_000_000 + i * 100))
    _sync(app, sid)
    assert [k for k, _n in _media(app, sid)] == ["b.png"]                                       # only the newest, no sub-folder
    (photos / "b.png").unlink()                                                                  # mirror is off: it stays in the library
    _sync(app, sid)
    assert [k for k, _n in _media(app, sid)] == ["a.png", "b.png"]                              # and the next newest takes the free place


def test_a_missing_folder_and_a_file_are_reported_in_words(app, photos, tmp_path):
    sid = _source(app, "folder", str(tmp_path / "nope"))
    status = _sync(app, sid)
    assert "cannot be found" in status
    with app.app_context():
        s = db.session.get(ImageSource, sid)
        assert s.last_ok is False and s.next_sync_at is not None
    sid2 = _source(app, "folder", str(photos / "a.png"), name="file")
    assert "not a folder" in _sync(app, sid2)


def test_broken_images_are_skipped_not_fatal(app, photos):
    (photos / "broken.png").write_bytes(b"this is not a png")
    sid = _source(app, "folder", str(photos))
    status = _sync(app, sid)
    assert "3 new" in status and "1 skipped" in status


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="no symlinks")
def test_a_link_out_of_the_folder_is_not_followed(app, photos, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.png").write_bytes(png_bytes())
    try:
        os.symlink(outside, photos / "link", target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need a privilege here")
    sid = _source(app, "folder", str(photos))
    _sync(app, sid)
    assert all("secret" not in k for k, _n in _media(app, sid))


# ---- a real web server on this machine: an index page, and a small WebDAV --------------------------------------------------
class _Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture
def web(photos):
    handler = lambda *a, **k: _Quiet(*a, directory=str(photos), **k)  # noqa: E731
    srv = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}/"
    srv.shutdown()


def test_a_web_folder_listing_is_read_with_sub_folders(app, web):
    sid = _source(app, "http", web)
    assert "3 new" in _sync(app, sid)
    assert [k for k, _n in _media(app, sid)] == ["a.png", "b.png", "sub/c.png"]
    assert "new" not in _sync(app, sid)


def test_a_web_folder_never_leaves_its_folder(app, photos, web):
    (photos / "sub" / "deeper.png").write_bytes(png_bytes())
    sid = _source(app, "http", web + "sub/")
    _sync(app, sid)
    assert [k for k, _n in _media(app, sid)] == ["c.png", "deeper.png"]


class _Dav(BaseHTTPRequestHandler):
    root = None
    def log_message(self, *a):
        pass

    def do_PROPFIND(self):
        base = self.path.rstrip("/")
        folder = Path(self.root) / base[len("/dav"):].lstrip("/")
        rows = ['<d:response><d:href>%s/</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>' % base]
        for p in sorted(folder.iterdir()):
            href = f"{base}/{p.name}" + ("/" if p.is_dir() else "")
            if p.is_dir():
                rows.append(f'<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:resourcetype><d:collection/></d:resourcetype></d:prop></d:propstat></d:response>')
            else:
                rows.append(f'<d:response><d:href>{href}</d:href><d:propstat><d:prop><d:getetag>"{p.stat().st_size}-{int(p.stat().st_mtime)}"</d:getetag>'
                            f'<d:getcontentlength>{p.stat().st_size}</d:getcontentlength><d:resourcetype/></d:prop></d:propstat></d:response>')
        body = ('<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">' + "".join(rows) + '</d:multistatus>').encode()
        self.send_response(207); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_GET(self):
        p = Path(self.root) / self.path.replace("/dav/", "", 1).lstrip("/")
        if not p.is_file():
            self.send_response(404); self.end_headers(); return
        data = p.read_bytes()
        self.send_response(200); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)


def test_webdav_is_listed_and_read(app, photos):
    _Dav.root = str(photos)
    srv = HTTPServer(("127.0.0.1", 0), _Dav)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sid = _source(app, "webdav", f"http://127.0.0.1:{srv.server_port}/dav/")
        assert "3 new" in _sync(app, sid)
        assert [k for k, _n in _media(app, sid)] == ["a.png", "b.png", "sub/c.png"]
    finally:
        srv.shutdown()


def test_a_nextcloud_share_link_becomes_its_public_webdav_address():
    from aisignx.sources.adapters import web
    src = ImageSource(location="https://cloud.example.com/nextcloud/s/AbC123xyz", username="")
    assert web._dav_target(src) == ("https://cloud.example.com/nextcloud/public.php/webdav/", "AbC123xyz")
    assert web._dav_target(ImageSource(location="https://h/remote.php/dav/files/me/"))[1] is None


# ---- the cloud kinds, against faked answers -------------------------------------------------------------------------------
def _fake_request(routes):
    def request(method, url, **kw):
        for needle, (status, payload) in routes.items():
            if needle in url:
                body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                if kw.get("sink") is not None:
                    kw["sink"].write(body)
                    return status, {}, b""
                return status, {}, body
        return 404, {}, b""
    return request


def test_google_drive_lists_a_shared_folder_and_needs_a_key(app, monkeypatch):
    listing = {"files": [{"id": "IMG1", "name": "one.jpg", "mimeType": "image/jpeg", "size": "10", "md5Checksum": "aa"},
                         {"id": "IMG2", "name": "two", "mimeType": "image/png", "modifiedTime": "2026-01-01"},
                         {"id": "DOC", "name": "x.pdf", "mimeType": "application/pdf"}]}
    monkeypatch.setattr(shttp, "request", _fake_request({"drive/v3/files?": (200, listing), "alt=media": (200, png_bytes())}))
    src = ImageSource(location="https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOp?usp=sharing", recursive=True)
    out = cloud.list_gdrive(src, "KEY", 50)
    assert [(r.key, r.name) for r in out] == [("IMG1", "one.jpg"), ("IMG2", "two.png")]
    with pytest.raises(shttp.SourceError, match="API key"):
        cloud.list_gdrive(src, "", 50)
    with pytest.raises(shttp.SourceError, match="link"):
        cloud.list_gdrive(ImageSource(location="not a link!", recursive=True), "KEY", 5)
    monkeypatch.setattr(shttp, "request", _fake_request({"drive/v3/files?": (403, {"error": {"errors": [{"reason": "keyInvalid"}]}})}))
    with pytest.raises(shttp.SourceError, match="keyInvalid"):
        cloud.list_gdrive(src, "BAD", 50)


def test_onedrive_walks_a_shared_folder(app, monkeypatch):
    root = {"value": [{"id": "F1", "name": "sub", "folder": {}}, {"id": "P1", "name": "p.jpg", "file": {"hashes": {"sha1Hash": "h1"}}, "size": 5,
                                                              "@content.downloadUrl": "https://dl.example/p1"}]}
    sub = {"value": [{"id": "P2", "name": "q.png", "file": {}, "eTag": "e2"}]}
    monkeypatch.setattr(shttp, "request", _fake_request({"/items/F1/children": (200, sub), "/root/children": (200, root)}))
    out = cloud.list_onedrive(ImageSource(location="https://1drv.ms/f/s!abc", recursive=True), "", 50)
    assert sorted((r.key, r.stamp) for r in out) == [("P1", "h1"), ("P2", "e2")]
    with pytest.raises(shttp.SourceError, match="https"):
        cloud.list_onedrive(ImageSource(location="http://x", recursive=True), "", 5)


def test_dropbox_reads_the_folder_zip_and_skips_bombs_and_other_files(app, monkeypatch):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("album/one.png", png_bytes())
        z.writestr("album/readme.txt", "x")
        z.writestr("album/big.png", b"0" * (cloud.MAX_IMAGE + 5))
    monkeypatch.setattr(shttp, "request", _fake_request({"dl=1": (200, buf.getvalue())}))
    out = cloud.list_dropbox(ImageSource(location="https://www.dropbox.com/scl/fo/abc/def?rlkey=k&dl=0", recursive=True), "", 50)
    assert [r.name for r in out] == ["one.png"]
    sink = io.BytesIO()
    out[0].fetch(sink)
    assert sink.getvalue().startswith(b"\x89PNG")
    with pytest.raises(shttp.SourceError, match="Dropbox"):
        cloud.list_dropbox(ImageSource(location="https://evil.example.com/sh/x", recursive=True), "", 5)


def test_flickr_feed(app, monkeypatch):
    feed = {"items": [{"title": "Sunset", "link": "https://www.flickr.com/photos/1/2", "date_taken": "2026-01-02T10:00:00-08:00",
                       "media": {"m": "https://live.staticflickr.com/65535/123_abc_m.jpg"}},
                      {"title": "Bad host", "media": {"m": "https://evil.example/x_m.jpg"}}]}
    monkeypatch.setattr(shttp, "request", _fake_request({"photos_public.gne": (200, feed)}))
    out = cloud.list_flickr(ImageSource(location="https://www.flickr.com/photos/12345678@N00/", recursive=True), "", 50)
    assert [r.name for r in out] == ["Sunset.jpg"]
    with pytest.raises(shttp.SourceError, match="user id"):
        cloud.list_flickr(ImageSource(location="nothing", recursive=True), "", 5)


# ---- the HTTP client's rules ----------------------------------------------------------------------------------------------
def test_the_client_refuses_metadata_and_private_addresses_for_cloud_kinds():
    with pytest.raises(shttp.SourceError):
        shttp.check_host("169.254.169.254", 80, allow_private=True)           # cloud metadata: never
    with pytest.raises(shttp.SourceError):
        shttp.check_host("127.0.0.1", 80, allow_private=False)
    with pytest.raises(shttp.SourceError):
        shttp.check_host("192.168.1.10", 80, allow_private=False)
    assert shttp.check_host("127.0.0.1", 80, allow_private=True) == "127.0.0.1"
    with pytest.raises(shttp.SourceError, match="http"):
        shttp.request("GET", "file:///etc/passwd")


# ---- the saved secret, the admin page, deleting ---------------------------------------------------------------------------
def test_the_secret_is_stored_encrypted_and_survives_a_round_trip(app):
    with app.app_context():
        s = ImageSource(name="x", kind="gdrive", location="1AbCdEfGhIjKl")
        service.set_secret(s, "AIza-super-secret")
        assert "AIza" not in s.secret and service.get_secret(s) == "AIza-super-secret"
        service.set_secret(s, "")
        assert s.secret is None and service.get_secret(s) == ""


def _csrf(client, path="/sources/"):
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', client.get(path).get_data(as_text=True)).group(1)


def test_the_page_is_admin_only_and_adds_syncs_and_removes(app, configured, photos):
    make_user(app, "ed", "editor")
    ed = app.test_client()
    login(ed, "ed")
    assert ed.get("/sources/").status_code == 403
    assert app.test_client().get("/sources/").status_code in (302, 401)
    make_user(app, "boss", "admin")
    admin = app.test_client()
    login(admin, "boss")
    tok = _csrf(admin)
    r = admin.post("/sources/new", data={"csrf_token": tok, "name": "Pics", "kind": "folder", "location": str(photos), "interval_min": "30",
                                         "max_images": "100", "recursive": "y", "mirror": "y", "enabled": "y", "secret": "pw-1"}, follow_redirects=True)
    assert r.status_code == 200 and b"Pics" in r.data
    assert admin.post("/sources/new", data={"csrf_token": tok, "name": "Pics", "kind": "folder", "location": "x", "interval_min": "30", "max_images": "5"}).status_code == 400
    assert admin.post("/sources/new", data={"csrf_token": tok, "name": "Bad", "kind": "folder", "location": "x", "interval_min": "1", "max_images": "5"}).status_code == 400
    with app.app_context():
        s = ImageSource.query.filter_by(name="Pics").one()
        sid = s.id
        assert s.secret and "pw-1" not in s.secret
    assert b"pw-1" not in admin.get(f"/sources/{sid}").data                                   # a saved secret is never shown again
    assert b"Found" not in admin.post(f"/sources/{sid}/test", data={"csrf_token": tok}).data
    r = admin.post(f"/sources/{sid}/test", data={"csrf_token": tok}, follow_redirects=True)
    assert b"found 3 entries" in r.data
    from aisignx import jobs
    with app.app_context():
        assert jobs.get_job("sources.sync").resolve()() is not None                            # due: the job syncs it
    assert len(_media(app, sid)) == 3
    r = admin.post(f"/sources/{sid}/delete", data={"csrf_token": tok}, follow_redirects=True)
    assert b"removed with its 3 imported images" in r.data
    with app.app_context():
        assert ImageSource.query.count() == 0 and Media.query.count() == 0


# ---- the Image library plugin ---------------------------------------------------------------------------------------------
def test_the_image_library_plugin_shows_a_connected_source(app, photos):
    from aisignx.plugins.bundled import image_library as lib
    from aisignx.plugins.schema import SchemaError
    sid = _source(app, "folder", str(photos))
    _sync(app, sid)
    with app.app_context():
        cfg = {"source": "connected", "image_source": sid, "source_count": 2, "source_order": "name"}
        assert [m.name for m in lib.resolve(cfg)] == ["a", "b"]
        assert lib.image_count(cfg) == 2 and lib.playable(None, cfg, None)
        assert len(lib.resolve({**cfg, "source_count": 50, "source_order": "newest"})) == 3
        assert not lib.playable(None, {**cfg, "image_source": 9999}, None)
        with pytest.raises(SchemaError):
            lib.normalize({"source": "connected"}, None)
        assert lib.normalize(cfg, None) == cfg


def test_the_plugin_form_offers_the_sources_and_validates_the_choice(app, configured, photos):
    from aisignx.plugins import schema
    from aisignx.plugins import service as plugin_service
    sid = _source(app, "folder", str(photos))
    with app.app_context():
        field = next(f for f in plugin_service.get_plugin("image_library").schema if f["name"] == "image_source")
        assert field["type"] == "image_source"
        assert schema.validate_value(field, str(sid)) == (sid, None)
        for bad in ("abc", "0", "9999"):
            value, error = schema.validate_value(field, bad)
            assert error, bad
