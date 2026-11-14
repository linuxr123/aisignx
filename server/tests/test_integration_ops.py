"""Phase 6 integration: jobs, the role matrix of the admin pages, API brute-force limits and
an end-to-end upload -> audit -> backup -> restore flow."""
from io import BytesIO
from pathlib import Path

import pytest

from aisignx import jobs, maintenance
from aisignx.backup import restore as restore_mod
from aisignx.backup import service as backup_service
from aisignx.extensions import db
from aisignx.models import AuditLog, Media

from .conftest import bearer, login, make_token, make_user, png_bytes, run_job


@pytest.fixture(autouse=True)
def _reset_backup_state():
    backup_service.wait(10)
    maintenance.end()
    yield
    backup_service.wait(10)
    maintenance.end()


# --- jobs -------------------------------------------------------------------------------

@pytest.mark.parametrize("name", [job.name for job in jobs.JOBS])
def test_every_job_runs_on_an_empty_database(app, name):
    run_job(app, name)  # raises on failure
    with app.app_context():
        row = next(s for s in jobs.status() if s["name"] == name)
    assert row["fail_count"] == 0 and row["last_error"] is None
    assert row["last_ok_at"] is not None


# --- role matrix (spec section 6) -------------------------------------------------------

ADMIN_ONLY = ["users.index", "settings.index", "alerts.settings", "audit.index",
              "audit.export", "backup.index", "backup.status", "system.index",
              "system.logs", "tokens.admin_index"]
EVERYONE = ["main.dashboard", "media.index", "playlists.index", "displays.index",
            "schedules.index", "groups.index", "pop.report", "alerts.index",
            "emergency.index", "auth.profile", "tokens.index"]


@pytest.fixture
def role_clients(configured):
    make_user(configured, "ed", "editor")
    make_user(configured, "vi", "viewer")
    clients = {}
    for role, name in (("admin", "admin"), ("editor", "ed"), ("viewer", "vi")):
        client = configured.test_client()
        assert login(client, name).status_code == 302
        clients[role] = client
    return clients


def _url(app, endpoint):
    with app.test_request_context():
        from flask import url_for
        return url_for(endpoint)


@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_nav_pages_follow_the_role_matrix(configured, role_clients, role):
    client = role_clients[role]
    for endpoint in EVERYONE:
        resp = client.get(_url(configured, endpoint))
        assert resp.status_code == 200, (role, endpoint)
    for endpoint in ADMIN_ONLY:
        resp = client.get(_url(configured, endpoint))
        expected = 200 if role == "admin" else 403
        assert resp.status_code == expected, (role, endpoint)
    page = client.get(_url(configured, "main.dashboard")).get_data(as_text=True)
    assert ("Audit log" in page) == (role == "admin")
    assert "API tokens" in page and "Alerts" in page and "Reports" in page


@pytest.mark.parametrize("role", ["admin", "editor", "viewer"])
def test_play_reports_csv_open_to_every_role(role_clients, role):
    resp = role_clients[role].get("/reports/plays.csv")
    assert resp.status_code == 200 and resp.mimetype == "text/csv"


# --- API brute force --------------------------------------------------------------------

def test_bearer_brute_force_gets_429(app, client, configured):
    app.config["LOGIN_MAX_PER_IP"] = 3  # limiters are created lazily from config
    uid = make_user(app, "ed", "editor")
    good = make_token(app, uid)
    assert client.get("/api/v1/media", headers=bearer(good)).status_code == 200
    for _ in range(3):
        bad = bearer("asx_" + "x" * 40)
        assert client.get("/api/v1/media", headers=bad).status_code == 401
    resp = client.get("/api/media", headers=bearer("asx_" + "y" * 40))
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "rate_limited"
    assert int(resp.headers["Retry-After"]) > 0
    # While the address is blocked even a valid token waits; other addresses are not affected.
    assert client.get("/api/v1/media", headers=bearer(good)).status_code == 429
    other = client.get("/api/v1/media", headers=bearer(good),
                       environ_base={"REMOTE_ADDR": "10.9.9.9"})
    assert other.status_code == 200


def test_missing_header_counts_as_a_failure(app, client, configured):
    app.config["LOGIN_MAX_PER_IP"] = 2
    for _ in range(2):
        assert client.get("/api/v1/media").status_code == 401
    assert client.get("/api/v1/media").status_code == 429


def test_valid_tokens_never_count(app, client, configured):
    app.config["LOGIN_MAX_PER_IP"] = 2
    token = make_token(app, make_user(app, "ed", "editor"))
    for _ in range(5):
        assert client.get("/api/v1/media", headers=bearer(token)).status_code == 200


# --- end to end -------------------------------------------------------------------------

def test_api_upload_audit_backup_restore(app, client, configured):
    uid = make_user(app, "ed", "editor")
    token = make_token(app, uid)
    resp = client.post("/api/media", headers=bearer(token), content_type="multipart/form-data",
                       data={"file": (BytesIO(png_bytes()), "slide.png", "image/png"),
                             "name": "Slide", "duration": "12"})
    assert resp.status_code == 201, resp.get_data(as_text=True)
    media_id = resp.get_json()["id"]

    with app.app_context():
        row = AuditLog.query.filter_by(action="media.upload", target_id=media_id).one()
        assert row.via == "api" and row.username == "ed" and row.token_id is not None
        stored = db.session.get(Media, media_id).stored_name
        archive = backup_service.create("manual")
    media_file = Path(app.config["UPLOAD_FOLDER"]) / "media" / stored

    # Replace through the API, then lose the row: the restore brings back the original.
    resp = client.post(f"/api/media/{media_id}/replace", headers=bearer(token),
                       content_type="multipart/form-data",
                       data={"file": (BytesIO(png_bytes(color=(0, 0, 255))), "b.png")})
    assert resp.status_code == 200
    assert not media_file.exists()
    with app.app_context():
        db.session.delete(db.session.get(Media, media_id))
        db.session.commit()

    with app.app_context():
        result = restore_mod.restore(archive, actor_via="cli")
    assert result["skipped"] == []
    with app.app_context():
        media = db.session.get(Media, media_id)
        assert media is not None and media.name == "Slide" and media.default_duration == 12
        assert media.stored_name == stored
        assert AuditLog.query.filter_by(action="backup.restore").count() == 1
    assert media_file.is_file()
    # The restored item is served to the API again with the same token (same database).
    resp = client.get(f"/api/v1/media/{media_id}", headers=bearer(token))
    assert resp.status_code == 200 and resp.get_json()["name"] == "Slide"
