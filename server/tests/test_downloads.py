"""Downloads page: any signed-in user downloads, only admins upload or delete."""
import hashlib
from io import BytesIO

from aisignx.extensions import db
from aisignx.models import Download

from .conftest import login, make_user

DATA = b"MZ" + b"x" * 5000


def _upload(client, name="Setup.exe", data=DATA, kind="windows-player", version="3.0.2"):
    return client.post("/downloads/upload", data={"file": (BytesIO(data), name), "kind": kind,
                                                  "version": version, "notes": "n"},
                       content_type="multipart/form-data", follow_redirects=True)


def test_requires_login(configured, client):
    assert client.get("/downloads/").status_code in (302, 401)


def test_admin_upload_and_any_user_download(admin_client, app):
    r = _upload(admin_client)
    assert b"Added Setup.exe" in r.data
    with app.app_context():
        row = Download.query.one()
        assert row.size == len(DATA) and row.sha256 == hashlib.sha256(DATA).hexdigest()
        assert row.stored_name != "Setup.exe"
        rid = row.id
    page = admin_client.get("/downloads/").data
    assert b"Setup.exe" in page and hashlib.sha256(DATA).hexdigest()[:16].encode() in page
    make_user(app, "view", "viewer")
    other = app.test_client()
    login(other, "view")
    got = other.get(f"/downloads/{rid}/get")
    assert got.status_code == 200 and got.data == DATA
    assert "attachment" in got.headers["Content-Disposition"]
    assert got.headers["X-Content-Type-Options"] == "nosniff"
    with app.app_context():
        assert db.session.get(Download, rid).download_count == 1


def test_viewer_cannot_upload_or_delete(admin_client, app):
    _upload(admin_client)
    with app.app_context():
        rid = Download.query.one().id
    make_user(app, "ed", "editor")
    c = app.test_client()
    login(c, "ed")
    assert _upload(c).status_code in (403, 200) and b"Added" not in _upload(c).data
    assert c.post(f"/downloads/{rid}/delete").status_code == 403
    with app.app_context():
        assert Download.query.count() == 1


def test_rejects_bad_types_and_empty_and_clean_names(admin_client, app):
    assert b"not allowed" in _upload(admin_client, name="evil.html").data
    assert b"empty" in _upload(admin_client, data=b"").data or b"Choose a file" in _upload(admin_client, data=b"").data
    _upload(admin_client, name=r"..\..\sneaky name.exe")
    with app.app_context():
        row = Download.query.one()
        assert "/" not in row.filename and "\\" not in row.filename and row.filename.endswith(".exe")


def test_delete_removes_file(admin_client, app):
    from aisignx.downloads.routes import downloads_dir
    _upload(admin_client)
    with app.app_context():
        row = Download.query.one()
        path = downloads_dir() / row.stored_name
        rid = row.id
        assert path.is_file()
    page = admin_client.get("/downloads/").data.decode()
    import re
    token = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page)
    r = admin_client.post(f"/downloads/{rid}/delete", data={"csrf_token": token.group(1) if token else ""})
    with app.app_context():
        assert Download.query.count() == 0 and not path.exists()


def test_cli_add_publishes_and_replaces(app, tmp_path):
    from aisignx.downloads.cli import downloads_cli
    f = tmp_path / "AiSignX-Player.apk"
    f.write_bytes(b"PK" + b"1" * 300)
    runner = app.test_cli_runner()
    r = runner.invoke(downloads_cli, ["add", str(f), "--kind", "android-player", "--version", "3.0.2"])
    assert r.exit_code == 0, r.output
    f.write_bytes(b"PK" + b"2" * 300)
    r = runner.invoke(downloads_cli, ["add", str(f), "--kind", "android-player", "--version", "3.0.3"])
    assert r.exit_code == 0, r.output
    with app.app_context():
        row, = Download.query.all()               # the older one was replaced
        assert row.version == "3.0.3" and row.uploaded_by == "cli"
    bad = tmp_path / "x.html"; bad.write_text("hi")
    assert runner.invoke(downloads_cli, ["add", str(bad), "--kind", "other"]).exit_code != 0
