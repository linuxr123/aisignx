"""Phase 6 foundation: config, migration 0007, jobs, audit.record, CSV, maintenance,
logging and the blueprint skeletons."""
import json
import logging
import os
from datetime import timedelta

import pytest
import sqlalchemy as sa
from flask import g
from flask_login import login_user
from flask_migrate import downgrade, upgrade

from aisignx import create_app, csvutil, dbinfo, jobs, maintenance
from aisignx.audit import service as audit
from aisignx.extensions import db
from aisignx.logging_setup import configure_logging, log_file_path
from aisignx.models import (Alert, ApiToken, AuditLog, Display, DisplayGroup, PairingCode,
                            Setting, User)
from aisignx.timeutil import utcnow

from .conftest import PASSWORD, login, make_token, make_user, run_job

PREVIOUS = "c4e8a2f61d37"
NEW_TABLES = {"audit_log", "api_tokens", "alerts", "play_events", "play_daily"}


# --- config -----------------------------------------------------------------------------

def test_instance_dir_and_data_folders_from_env(tmp_path, monkeypatch):
    for name in ("AISIGNX_LOG_DIR", "AISIGNX_BACKUP_DIR", "AISIGNX_JOBS", "AISIGNX_LOG_LEVEL",
                 "AISIGNX_LOGIN_WINDOW_MIN", "AISIGNX_LOGIN_MAX_PER_IP", "AISIGNX_SMTP_PASSWORD",
                 "AISIGNX_DATABASE_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AISIGNX_SECRET_KEY", "test-secret")
    monkeypatch.setenv("AISIGNX_INSTANCE_DIR", str(tmp_path / "data" / "instance"))
    monkeypatch.setenv("AISIGNX_LOGIN_MAX_PER_USER", "7")
    app = create_app({"TESTING": True})
    data = tmp_path / "data"
    assert app.instance_path == os.path.abspath(data / "instance")
    assert os.path.isdir(app.instance_path)
    assert app.config["SQLALCHEMY_DATABASE_URI"].endswith("data/instance/aisignx.db")
    assert app.config["LOG_DIR"] == str(data / "logs")
    assert app.config["BACKUP_DIR"] == str(data / "backups")
    assert app.config["JOBS_ENABLED"] is True
    assert app.config["LOGIN_MAX_PER_USER"] == 7
    assert app.config["LOGIN_MAX_PER_IP"] == 20
    assert app.config["LOGIN_WINDOW_SECONDS"] == 15 * 60
    assert app.config["SMTP_PASSWORD"] is None
    assert app.config["LOG_LEVEL"] == "INFO" and app.config["BROWSER_NO_SANDBOX"] is False


def test_setting_typed_helpers(app):
    with app.app_context():
        assert Setting.get_int("backup.keep") == 7
        Setting.set("backup.keep", "abc")
        assert Setting.get_int("backup.keep") == 7
        Setting.set("backup.keep", "500")
        assert Setting.get_int("backup.keep", lo=1, hi=100) == 7
        Setting.set("backup.keep", " 12 ")
        assert Setting.get_int("backup.keep", lo=1, hi=100) == 12
        assert Setting.get_int("no.such.key") is None
        assert Setting.get_bool("backup.enabled") is True
        assert Setting.get_bool("pop.enabled") is False
        now = utcnow()
        Setting.set_datetime("backup.last_ok_at", now)
        assert Setting.get_datetime("backup.last_ok_at") == now
        Setting.set("backup.last_ok_at", "2026-11-12T10:00:00Z")
        assert Setting.get_datetime("backup.last_ok_at").isoformat() == "2026-11-12T10:00:00+00:00"
        Setting.set("backup.last_ok_at", "garbage")
        assert Setting.get_datetime("backup.last_ok_at") is None


# --- migration 0007 ---------------------------------------------------------------------

def _tables():
    return set(sa.inspect(db.engine).get_table_names())


def _display_columns():
    return {c["name"] for c in sa.inspect(db.engine).get_columns("displays")}


def test_migration_downgrades_and_upgrades_cleanly(app):
    with app.app_context():
        display = Display(name="Lobby")
        db.session.add(display)
        db.session.flush()
        db.session.add(PairingCode(code="ABC123", secret_hash="s" * 64,
                                   expires_at=utcnow() + timedelta(minutes=5),
                                   display_id=display.id))
        db.session.commit()
        display_id = display.id
        assert dbinfo.current_revision() == dbinfo.head_revision()
        assert {PREVIOUS, dbinfo.head_revision()} <= dbinfo.known_revisions()
        assert NEW_TABLES <= _tables() and "alerts_muted" in _display_columns()
        db.session.remove()

        downgrade(revision=PREVIOUS)
        db.engine.dispose()
        assert dbinfo.current_revision() == PREVIOUS
        assert not NEW_TABLES & _tables()
        assert "alerts_muted" not in _display_columns()

        upgrade()
        db.engine.dispose()
        assert dbinfo.current_revision() == dbinfo.head_revision()
        assert NEW_TABLES <= _tables()
        # Existing rows survive; pairing codes are not cascade-deleted.
        assert db.session.get(Display, display_id).alerts_muted is False
        assert PairingCode.query.count() == 1


def test_dbinfo(app):
    with app.app_context():
        assert dbinfo.dialect() == "sqlite"
        assert dbinfo.sqlite_path().name == "test.db"
        assert dbinfo.sqlite_path().is_absolute()


def test_alert_open_index_is_partial(app):
    with app.app_context():
        db.session.add(Alert(kind="disk_low", key="disk:uploads", title="Low", resolved_at=utcnow()))
        db.session.add(Alert(kind="disk_low", key="disk:uploads", title="Low", resolved_at=utcnow()))
        db.session.add(Alert(kind="disk_low", key="disk:uploads", title="Low"))
        db.session.commit()
        db.session.add(Alert(kind="disk_low", key="disk:uploads", title="Again"))
        with pytest.raises(sa.exc.IntegrityError):
            db.session.commit()
        db.session.rollback()


# --- jobs -------------------------------------------------------------------------------

CALLS: list[str] = []


def ok_job():
    CALLS.append("ok")
    return "fine"


def bad_job():
    CALLS.append("bad")
    raise RuntimeError("disk on fire")


@pytest.fixture
def test_jobs(monkeypatch):
    CALLS.clear()
    monkeypatch.setattr(jobs, "JOBS", (
        jobs.Job("t.bad", 60, f"{__name__}:bad_job"),
        jobs.Job("t.ok", 60, f"{__name__}:ok_job"),
    ))
    return CALLS


def _status(app, name):
    with app.app_context():
        return next(s for s in jobs.status() if s["name"] == name)


def test_registry_lists_every_job():
    names = [j.name for j in jobs.JOBS]
    assert names == ["alerts.sweep", "alerts.mail", "alerts.purge", "backup.scheduled",
                     "audit.purge", "pop.rollup", "media.orphans", "media.probe", "media.webpages", "agent.commands", "updates.check", "emergency.schedule", "plugin_cache_prune", "sources.sync"]
    assert all(":" in j.target and j.target.startswith("aisignx.") for j in jobs.JOBS)


def test_is_due():
    job = jobs.Job("x", 60, "m:f")
    t0 = utcnow()
    assert jobs.is_due(job, None, t0)
    assert not jobs.is_due(job, t0, t0 + timedelta(seconds=59))
    assert jobs.is_due(job, t0, t0 + timedelta(seconds=60))
    assert jobs.is_due(job, t0 + timedelta(hours=1), t0)  # clock moved back


def test_run_due_with_mocked_clock(app, test_jobs):
    t0 = utcnow()
    assert jobs.run_due(app, now=t0) == ["t.bad", "t.ok"]
    assert jobs.run_due(app, now=t0 + timedelta(seconds=30)) == []
    assert jobs.run_due(app, now=t0 + timedelta(seconds=61)) == ["t.bad", "t.ok"]
    assert test_jobs == ["bad", "ok", "bad", "ok"]


def test_claim_with_stale_value_fails(app):
    t0 = utcnow()
    with app.app_context():
        assert jobs.claim("t.x", None, t0) is True
        assert jobs.claim("t.x", None, t0) is False  # someone else inserted the row
        current = Setting.get("job.t.x.last_run_at")
        t1 = t0 + timedelta(minutes=1)
        assert jobs.claim("t.x", "2000-01-01T00:00:00+00:00", t1) is False
        assert jobs.claim("t.x", current, t1) is True
        assert jobs.claim("t.x", current, t1) is False  # second claim with the old value
        db.session.expire_all()
        assert Setting.get_datetime("job.t.x.last_run_at") == t1


def test_failing_job_is_recorded_and_loop_survives(app, test_jobs):
    t0 = utcnow()
    jobs.run_due(app, now=t0)
    bad, ok = _status(app, "t.bad"), _status(app, "t.ok")
    assert bad["fail_count"] == 1 and "disk on fire" in bad["last_error"]
    assert bad["last_ok_at"] is None and bad["last_run_at"] is not None
    assert ok["fail_count"] == 0 and ok["last_error"] is None and ok["last_ok_at"]

    with pytest.raises(RuntimeError):
        run_job(app, "t.bad")
    assert _status(app, "t.bad")["fail_count"] == 2

    jobs.JOBS = (jobs.Job("t.bad", 60, f"{__name__}:ok_job"),)
    assert run_job(app, "t.bad") == "fine"
    bad = _status(app, "t.bad")
    assert bad["fail_count"] == 0 and bad["last_error"] is None and bad["last_ok_at"]


def test_last_run_survives_restart(app, test_jobs, tmp_path, monkeypatch):
    assert jobs.run_now(app, "t.ok") == "fine"
    first = _status(app, "t.ok")["last_run_at"]
    app2 = create_app({"TESTING": True, "JOBS_ENABLED": False,
                       "SQLALCHEMY_DATABASE_URI": app.config["SQLALCHEMY_DATABASE_URI"],
                       "LOG_DIR": str(tmp_path / "logs2")})
    try:
        assert _status(app2, "t.ok")["last_run_at"] == first
        assert jobs.run_now(app2, "t.ok") is None  # not due yet
        assert test_jobs == ["ok"]
    finally:
        with app2.app_context():
            db.engine.dispose()


def test_ensure_started_is_a_no_op_in_tests(app):
    app.config["JOBS_ENABLED"] = True
    jobs.ensure_started(app)
    assert jobs.THREAD_KEY not in app.extensions
    assert jobs.loop_age_s(app) is None
    app.config.update(TESTING=False, JOBS_ENABLED=False)
    try:
        jobs.ensure_started(app)
        assert jobs.THREAD_KEY not in app.extensions
    finally:
        app.config["TESTING"] = True


def test_loop_age(app, monkeypatch):
    app.extensions[jobs.LOOP_KEY] = 1000.0
    monkeypatch.setattr(jobs.time, "time", lambda: 1012.5)
    assert jobs.loop_age_s(app) == 12.5


# --- audit.record -----------------------------------------------------------------------

def _rows():
    return AuditLog.query.order_by(AuditLog.id).all()


def test_record_commits_with_the_change_and_flushes_new_targets(app):
    with app.app_context():
        group = DisplayGroup(name="Floor 1")
        db.session.add(group)
        audit.record("group.create", group, detail={"name": "Floor 1"})
        assert group.id is not None  # flushed to get the id
        db.session.commit()
        (row,) = _rows()
        assert (row.action, row.target_type, row.target_id, row.target_name) == \
            ("group.create", "displaygroup", group.id, "Floor 1")
        assert row.via == "system" and row.user_id is None and row.ts is not None
        assert row.detail_data == {"name": "Floor 1"}


def test_record_rolls_back_with_the_caller(app):
    with app.app_context():
        db.session.add(DisplayGroup(name="Gone"))
        audit.record("group.create", ("group", 99, "Gone"))
        db.session.rollback()
        assert _rows() == [] and DisplayGroup.query.count() == 0


def test_record_redacts_and_caps(app):
    with app.app_context():
        audit.record("x.secret", detail={
            "name": "ok", "password": "hunter2",
            "nested": {"api_token": "abc", "list": [{"unlock_pin": "1234", "keep": 1}]},
            "long": "y" * 2000,
        })
        audit.record("x.big", detail={"items": ["z" * 400] * 50})
        db.session.commit()
        secret, big = _rows()
        data = secret.detail_data
        assert data["name"] == "ok" and data["password"] == "***"
        assert data["nested"]["api_token"] == "***"
        assert data["nested"]["list"][0] == {"unlock_pin": "***", "keep": 1}
        assert len(data["long"]) <= audit.MAX_STRING + 3
        assert "hunter2" not in secret.detail and "1234" not in secret.detail
        assert len(big.detail.encode("utf-8")) <= 4096
        assert json.loads(big.detail)["truncated"] is True


def test_record_in_web_and_api_requests(app):
    uid = make_user(app, "alice", "editor")
    with app.test_request_context("/", environ_base={"REMOTE_ADDR": "10.0.0.5"}):
        login_user(db.session.get(User, uid))
        audit.record_now("display.reload", ("display", 3, "Lobby"))
    raw = make_token(app, uid)
    with app.test_request_context("/api/v1/media", environ_base={"REMOTE_ADDR": "10.0.0.6"}):
        token = ApiToken.query.one()
        token_id = token.id
        g.api_user, g.api_token = db.session.get(User, uid), token
        audit.record_now("media.upload", ("media", 1, "clip"))
    with app.app_context():
        web, api = _rows()
        assert (web.via, web.username, web.user_id, web.ip) == ("web", "alice", uid, "10.0.0.5")
        assert (api.via, api.username, api.token_id, api.ip) == ("api", "alice", token_id,
                                                                 "10.0.0.6")
        assert raw.startswith("asx_") and ApiToken.query.one().prefix == raw[:12]


def test_record_from_cli_uses_explicit_user(app):
    uid = make_user(app, "bob", "admin")

    @app.cli.command("audit-probe")
    def probe():
        audit.record_now("backup.create", user=db.session.get(User, uid))

    result = app.test_cli_runner().invoke(args=["audit-probe"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        (row,) = _rows()
        assert (row.via, row.username) == ("cli", "bob")


def test_purge(app):
    with app.app_context():
        old = utcnow() - timedelta(days=40)
        for i in range(7):
            db.session.add(AuditLog(ts=old, action="x.old"))
        db.session.add(AuditLog(ts=utcnow(), action="x.new"))
        db.session.commit()
        Setting.set("audit.retention_days", "0")
        db.session.commit()
    assert run_job(app, "audit.purge") == "retention off"
    with app.app_context():
        assert AuditLog.query.count() == 8
        assert audit.purge(30, batch=3) == 7
        assert [r.action for r in _rows()] == ["x.new"]


def test_actions_catalog():
    for action in ("auth.login", "auth.rate_limited", "media.replace", "token.revoke",
                   "backup.restore", "alert.unmute", "alerts.settings", "smtp.test",
                   "pop.settings", "audit.settings", "display.identify", "playlist.reorder"):
        assert action in audit.ACTIONS
    assert audit.action_label("auth.login") == "Signed in"
    assert audit.action_label("unknown.thing") == "unknown.thing"


# --- CSV --------------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    ("=1+1", "'=1+1"), ("+cmd", "'+cmd"), ("-2", "'-2"), ("@SUM(A1)", "'@SUM(A1)"),
    ("\tx", "'\tx"), ("\rx", "'\rx"), ("plain", "plain"), ("a=b", "a=b"),
    (-2, "-2"), (None, ""), (True, "yes"),
])
def test_safe_cell(value, expected):
    assert csvutil.safe_cell(value) == expected


def test_stream_csv(app):
    with app.test_request_context("/"):
        rows = ((i, f"=evil{i}") for i in range(450))
        resp = csvutil.stream_csv("plays report.csv", ["id", "name"], rows)
        assert resp.is_streamed
        assert resp.headers["Content-Type"] == "text/csv; charset=utf-8"
        assert resp.headers["Content-Disposition"] == 'attachment; filename="plays_report.csv"'
        body = resp.get_data(as_text=True)
    assert body.startswith("﻿id,name\r\n0,'=evil0\r\n")
    assert body.count("\r\n") == 451


# --- maintenance ------------------------------------------------------------------------

def test_maintenance_returns_503(admin_client, paired_display):
    _display_id, token = paired_display
    maintenance.begin("restore")
    try:
        resp = admin_client.get("/media/")
        assert resp.status_code == 503 and resp.headers["Retry-After"] == "30"
        assert b"maintenance" in resp.data
        resp = admin_client.get("/api/player/v1/manifest",
                                headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 503 and resp.get_json()["error"] == "maintenance"
        assert admin_client.get("/healthz").status_code == 200
        assert admin_client.get("/admin/backups/status").status_code == 200
    finally:
        maintenance.end()
    assert maintenance.active() is None
    assert admin_client.get("/media/").status_code == 200


# --- logging ----------------------------------------------------------------------------

def test_configure_logging_does_not_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("AISIGNX_SECRET_KEY", "test-secret")
    config = {"TESTING": True, "LOG_TO_FILE": True, "LOG_DIR": str(tmp_path / "logs"),
              "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'l.db').as_posix()}"}
    loggers = [logging.getLogger("aisignx"), logging.getLogger("waitress")]
    try:
        create_app(config)
        app = create_app(config)
        for logger in loggers:
            assert sum(getattr(h, "_aisignx", False) for h in logger.handlers) == 1
        logging.getLogger("aisignx.jobs").warning("hello from the test")
        for logger in loggers:
            for h in logger.handlers:
                h.flush()
        text = log_file_path(app).read_text(encoding="utf-8")
        assert " WARNING aisignx.jobs: hello from the test" in text
        assert text[:20].endswith("Z") and text[10] == "T"
    finally:
        configure_logging(create_app({**config, "LOG_TO_FILE": False}))
    for logger in loggers:
        assert not any(getattr(h, "_aisignx", False) for h in logger.handlers)


# --- skeleton pages and nav -------------------------------------------------------------

STUB_PAGES = ["/admin/audit/", "/admin/backups/", "/alerts/", "/alerts/settings",
              "/admin/system/", "/admin/system/logs", "/tokens/", "/tokens/all",
              "/reports/plays"]
ADMIN_ONLY = {"/admin/audit/", "/admin/backups/", "/alerts/settings", "/admin/system/",
              "/admin/system/logs", "/tokens/all"}


def test_stub_pages_render_for_admin(admin_client):
    for url in STUB_PAGES:
        assert admin_client.get(url).status_code == 200, url
    status = admin_client.get("/admin/backups/status")
    assert status.status_code == 200 and status.get_json()["running"] is False
    html = admin_client.get("/dashboard").get_data(as_text=True)
    for label in ("Alerts", "Reports", "Admin", "Audit log", "Backups", "System", "Logs",
                  "All API tokens", "API tokens"):
        assert label in html, label
    for url in STUB_PAGES:
        if url != "/alerts/settings":  # linked from the alerts page, not the nav
            assert f'href="{url}"' in html, url


def test_stub_pages_respect_roles(configured, client):
    make_user(configured, "vera", "viewer")
    login(client, "vera", PASSWORD)
    for url in STUB_PAGES:
        expected = 403 if url in ADMIN_ONLY else 200
        assert client.get(url).status_code == expected, url
    assert client.get("/admin/backups/status").status_code == 403
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Audit log" not in html and 'href="/tokens/"' in html


def test_alert_badge_counts_open_unacknowledged(admin_client, configured):
    with configured.app_context():
        db.session.add_all([
            Alert(kind="disk_low", key="disk:a", title="A"),
            Alert(kind="disk_low", key="disk:b", title="B"),
            Alert(kind="disk_low", key="disk:c", title="C", acknowledged_at=utcnow()),
            Alert(kind="disk_low", key="disk:d", title="D", resolved_at=utcnow()),
        ])
        db.session.commit()
    html = admin_client.get("/dashboard").get_data(as_text=True)
    assert 'data-alert-badge title="Open alerts">2</span>' in html


def test_api_blueprint_is_csrf_exempt(tmp_path, monkeypatch):
    from aisignx.extensions import csrf

    from .conftest import make_csrf_app

    app = make_csrf_app(tmp_path, monkeypatch)
    try:
        assert "api" in {bp.name for bp in csrf._exempt_blueprints}
        assert app.config["JOBS_ENABLED"] is False
    finally:
        with app.app_context():
            db.engine.dispose()
