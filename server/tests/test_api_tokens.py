"""API tokens: creation, the Bearer check on /api/v1/media, and the token pages."""
import hashlib
from datetime import timedelta
from io import BytesIO

import pytest

from aisignx.extensions import db
from aisignx.models import ApiToken, AuditLog, Media, User
from aisignx.timeutil import as_utc, utcnow
from aisignx.tokens import service

from .conftest import (PASSWORD, bearer, csrf_token_from, login, make_csrf_app, make_token,
                       make_user, png_bytes)


def _user(app, user_id) -> User:
    return db.session.get(User, user_id)


def _png_post(client, token, path="/api/v1/media"):
    data = {"file": (BytesIO(png_bytes()), "a.png")}
    return client.post(path, data=data, content_type="multipart/form-data",
                       headers=bearer(token))


# --- service ----------------------------------------------------------------------------

def test_create_stores_only_hash_and_prefix(app, configured):
    with app.app_context():
        admin = User.query.filter_by(username="admin").one()
        token, raw = service.create(admin, " Laptop ", ["media:write", "media:read"], 30)
        db.session.commit()
        assert raw.startswith("asx_") and len(raw) > 40
        row = db.session.get(ApiToken, token.id)
        assert row.name == "Laptop"
        assert row.prefix == raw[:12]
        assert row.token_hash == hashlib.sha256(raw.encode()).hexdigest()
        assert row.scopes == "media:read media:write"
        assert raw not in {row.name, row.prefix, row.token_hash, row.scopes}
        assert as_utc(row.expires_at) - utcnow() > timedelta(days=29)
        audit = AuditLog.query.filter_by(action="token.create").one()
        assert audit.target_id == token.id and raw not in (audit.detail or "")
        assert row.prefix in audit.detail


def test_create_validates_scopes(app, configured):
    viewer_id = make_user(app, "vic", "viewer")
    with app.app_context():
        viewer = _user(app, viewer_id)
        with pytest.raises(ValueError):
            service.create(viewer, "x", ["media:write"], None)
        with pytest.raises(ValueError):
            service.create(viewer, "x", [], None)
        with pytest.raises(ValueError):
            service.create(viewer, "x", ["media:read", "admin:all"], None)
        with pytest.raises(ValueError):
            service.create(viewer, "  ", ["media:read"], None)
        token, _raw = service.create(viewer, "read", ["media:read"], None)
        assert token.expires_at is None


# --- Bearer check -----------------------------------------------------------------------

def test_valid_token_lists_media(app, client, configured):
    uid = make_user(app, "ed", "editor")
    raw = make_token(app, uid, scopes="media:read")
    resp = client.get("/api/v1/media", headers=bearer(raw))
    assert resp.status_code == 200
    assert resp.get_json() == {"items": [], "page": 1, "per_page": 50, "total": 0}
    assert resp.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("kind", ["revoked", "expired", "unknown", "inactive", "short",
                                  "missing", "basic"])
def test_bad_tokens_get_401(app, client, configured, kind):
    uid = make_user(app, "ed", "editor", active=kind != "inactive")
    extra = {"revoked": {"revoked_at": utcnow()},
             "expired": {"expires_at": utcnow() - timedelta(seconds=1)}}.get(kind, {})
    raw = make_token(app, uid, **extra)
    headers = {"unknown": bearer("asx_" + "x" * 40), "short": bearer("asx_short"),
               "missing": {}, "basic": {"Authorization": f"Basic {raw}"}}.get(kind, bearer(raw))
    resp = client.get("/api/v1/media", headers=headers)
    assert resp.status_code == 401
    assert resp.get_json() == {"error": "invalid_token",
                               "message": "API token missing, invalid, expired or revoked."}
    assert resp.headers["WWW-Authenticate"].startswith("Bearer")


def test_empty_scopes_are_denied(app, client, configured):
    uid = make_user(app, "ed", "editor")
    raw = make_token(app, uid, scopes="")
    resp = client.get("/api/v1/media", headers=bearer(raw))
    assert resp.status_code == 403 and resp.get_json()["error"] == "insufficient_scope"


def test_read_token_cannot_write(app, client, configured):
    uid = make_user(app, "ed", "editor")
    raw = make_token(app, uid, scopes="media:read")
    resp = _png_post(client, raw)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "insufficient_scope",
                               "message": "This token lacks media:write."}
    with app.app_context():
        assert Media.query.count() == 0


def test_demoted_editor_loses_write_at_once(app, client, configured):
    uid = make_user(app, "ed", "editor")
    raw = make_token(app, uid)
    assert _png_post(client, raw).status_code == 201
    with app.app_context():
        _user(app, uid).role = "viewer"
        db.session.commit()
    resp = _png_post(client, raw)
    assert resp.status_code == 403
    assert resp.get_json() == {"error": "forbidden",
                               "message": "Your role can no longer upload media."}
    assert client.get("/api/v1/media", headers=bearer(raw)).status_code == 200


def test_session_cookie_is_not_enough(app, admin_client):
    assert admin_client.get("/api/v1/media").status_code == 401
    assert admin_client.get("/api/media").status_code == 401


def test_bearer_post_needs_no_csrf(tmp_path, monkeypatch):
    app = make_csrf_app(tmp_path, monkeypatch)
    try:
        uid = make_user(app, "ed", "editor")
        raw = make_token(app, uid)
        client = app.test_client()
        resp = _png_post(client, raw, "/api/media")
        assert resp.status_code == 201, resp.get_data(as_text=True)
    finally:
        with app.app_context():
            db.engine.dispose()


def test_last_used_is_throttled(app, client, configured):
    uid = make_user(app, "ed", "editor")
    raw = make_token(app, uid, scopes="media:read")

    def last_used():
        with app.app_context():
            token = ApiToken.query.one()
            return as_utc(token.last_used_at), token.last_used_ip

    client.get("/api/v1/media", headers=bearer(raw), environ_base={"REMOTE_ADDR": "10.0.0.5"})
    first, ip = last_used()
    assert first is not None and ip == "10.0.0.5"
    client.get("/api/v1/media", headers=bearer(raw), environ_base={"REMOTE_ADDR": "10.0.0.6"})
    assert last_used() == (first, "10.0.0.5")  # within 60 s: not written again

    with app.app_context():
        ApiToken.query.one().last_used_at = utcnow() - timedelta(seconds=61)
        db.session.commit()
    client.get("/api/v1/media", headers=bearer(raw), environ_base={"REMOTE_ADDR": "10.0.0.6"})
    later, ip = last_used()
    assert later > first - timedelta(seconds=1) and ip == "10.0.0.6"


def test_api_upload_is_audited_with_token(app, client, configured):
    uid = make_user(app, "ed", "editor")
    raw = make_token(app, uid)
    media_id = _png_post(client, raw).get_json()["id"]
    with app.app_context():
        row = AuditLog.query.filter_by(action="media.upload").one()
        token = ApiToken.query.one()
        assert (row.user_id, row.via, row.token_id) == (uid, "api", token.id)
        assert (row.target_type, row.target_id) == ("media", media_id)


# --- pages ------------------------------------------------------------------------------

def test_create_token_page_shows_raw_once(app, configured, client):
    make_user(app, "ed", "editor")
    login(client, "ed")
    page = client.get("/tokens/").get_data(as_text=True)
    assert 'value="media:write"' in page and 'value="media:read"' in page
    resp = client.post("/tokens/", data={"name": "PowerPoint", "scopes": ["media:write"],
                                         "expires": "never"})
    assert resp.status_code == 200 and resp.headers["Cache-Control"] == "no-store"
    html = resp.get_data(as_text=True)
    with app.app_context():
        token = ApiToken.query.one()
        assert token.scopes == "media:write" and token.expires_at is None
    raw = html.split('id="raw-token" type="text" value="')[1].split('"')[0]
    assert hashlib.sha256(raw.encode()).hexdigest() == token.token_hash
    listing = client.get("/tokens/").get_data(as_text=True)
    assert "PowerPoint" in listing and token.prefix in listing and raw not in listing
    # the raw token works against the API, within its scope
    assert _png_post(client, raw).status_code == 201
    assert client.get("/api/v1/media", headers=bearer(raw)).status_code == 403


def test_viewer_cannot_create_write_token(app, configured, client):
    make_user(app, "vic", "viewer")
    login(client, "vic")
    page = client.get("/tokens/").get_data(as_text=True)
    assert 'value="media:write"' not in page
    resp = client.post("/tokens/", data={"name": "x", "scopes": ["media:write"], "expires": "30"})
    assert resp.status_code == 200
    with app.app_context():
        assert ApiToken.query.count() == 0
    client.post("/tokens/", data={"name": "x", "scopes": ["media:read"], "expires": "30"})
    with app.app_context():
        assert ApiToken.query.one().scopes == "media:read"


def test_revoke_own_and_admin(app, configured, client):
    ed = make_user(app, "ed", "editor")
    other = make_user(app, "olga", "editor")
    mine = make_token(app, ed)
    make_token(app, other)
    with app.app_context():
        mine_id = ApiToken.query.filter_by(user_id=ed).one().id
        other_id = ApiToken.query.filter_by(user_id=other).one().id
    login(client, "ed")
    assert client.post(f"/tokens/{other_id}/revoke").status_code == 404
    assert client.get("/tokens/all").status_code == 403
    assert client.post(f"/tokens/{mine_id}/revoke").status_code == 302
    assert client.get("/api/v1/media", headers=bearer(mine)).status_code == 401

    client.post("/logout")
    login(client, "admin")
    html = client.get("/tokens/all").get_data(as_text=True)
    assert "olga" in html and "Revoked" in html
    resp = client.post(f"/tokens/{other_id}/revoke", data={"back": "all"})
    assert resp.headers["Location"].endswith("/tokens/all")
    with app.app_context():
        assert ApiToken.query.filter(ApiToken.revoked_at.is_(None)).count() == 0
        assert AuditLog.query.filter_by(action="token.revoke").count() == 2


def test_token_pages_keep_csrf(tmp_path, monkeypatch):
    app = make_csrf_app(tmp_path, monkeypatch)
    try:
        make_user(app, "admin", "admin")
        make_user(app, "ed", "editor")
        client = app.test_client()
        login_page = client.get("/login").get_data(as_text=True)
        client.post("/login", data={"username": "ed", "password": PASSWORD,
                                    "csrf_token": csrf_token_from(login_page)})
        assert client.post("/tokens/", data={"name": "x", "scopes": ["media:read"],
                                             "expires": "30"}).status_code == 400
        with app.app_context():
            assert ApiToken.query.count() == 0  # no CSRF token: nothing created
        token = csrf_token_from(client.get("/tokens/").get_data(as_text=True))
        client.post("/tokens/", data={"name": "x", "scopes": ["media:read"], "expires": "30",
                                      "csrf_token": token})
        with app.app_context():
            assert ApiToken.query.count() == 1
    finally:
        with app.app_context():
            db.engine.dispose()
