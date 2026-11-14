"""System health (WP7): self-test, the System and Logs pages, log tail, /healthz, CLI."""
import json
import threading

import pytest

from aisignx import jobs
from aisignx.extensions import db
from aisignx.logging_setup import log_file_path
from aisignx.models import Setting
from aisignx.system import checks
from aisignx.system.logs import BLOCK, level_of, tail
from aisignx.timeutil import utcnow

from .conftest import login, make_user


def _by_name(rows):
    """Check rows by name. Runtime facts reuse names such as "Logs folder", so skip them."""
    return {c.name: c for c in rows if c.section != "runtime"}


def _line(i, level="INFO", name="aisignx.test"):
    return f"2026-11-12T10:00:{i % 60:02d}Z {level:<7} {name}: message number {i}"


# --- Pages and roles ----------------------------------------------------------------------

def test_admin_sees_system_page(admin_client):
    resp = admin_client.get("/admin/system/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    for name in ("Database", "Database schema", "Uploads folder", "Backups folder",
                 "Logs folder", "Last backup", "Job audit.purge", "AiSignX version", "CPUs"):
        assert name in html
    assert "Run self-test" in html


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_non_admin_forbidden(configured, client, role):
    make_user(configured, "someone", role)
    login(client, "someone")
    for url in ("/admin/system/", "/admin/system/logs", "/admin/system/logs/download"):
        assert client.get(url).status_code == 403
    assert client.post("/admin/system/selftest").status_code == 403


def test_selftest_post_includes_smtp(admin_client, monkeypatch, app):
    with app.app_context():
        Setting.set("smtp.host", "mail.example.test")
        db.session.commit()
    calls = []

    def fake_check():
        calls.append(1)
        return False, "ConnectionRefusedError: refused"

    import aisignx.alerts.mailer as mailer
    monkeypatch.setattr(mailer, "check_connection", fake_check)
    resp = admin_client.post("/admin/system/selftest")
    assert resp.status_code == 200 and calls == [1]
    assert "ConnectionRefusedError: refused" in resp.get_data(as_text=True)
    # The plain page does not connect.
    admin_client.get("/admin/system/")
    assert calls == [1]


def test_selftest_requires_csrf(tmp_path, monkeypatch):
    from .conftest import make_csrf_app

    app = make_csrf_app(tmp_path, monkeypatch)
    make_user(app, "admin", "admin")
    client = app.test_client()
    from .conftest import csrf_token_from

    token = csrf_token_from(client.get("/login").get_data(as_text=True))
    client.post("/login", data={"username": "admin", "password": "correct-horse-battery",
                                "csrf_token": token})
    assert client.post("/admin/system/selftest").status_code == 400
    page = client.get("/admin/system/").get_data(as_text=True)
    resp = client.post("/admin/system/selftest", data={"csrf_token": csrf_token_from(page)})
    assert resp.status_code == 200


# --- Self-test structure ------------------------------------------------------------------

def test_selftest_structure(app):
    with app.app_context():
        rows = checks.run_selftest(live=True)
    names = _by_name(rows)
    assert names["Database"].ok and names["Database schema"].ok
    assert names["Uploads folder"].ok and names["Logs folder"].ok and names["Backups folder"].ok
    assert any(n.startswith("Free disk space") for n in names)
    assert all(f"Job {j.name}" in names for j in jobs.JOBS)
    assert all(c.duration_ms is not None and c.duration_ms >= 0 for c in rows)
    assert names["Schedule ticker"].ok  # turned off in tests: informational
    assert not names["Last backup"].ok  # no backup yet
    assert names["E-mail (SMTP)"].ok and names["E-mail (SMTP)"].info
    runtime = [c for c in rows if c.section == "runtime"]
    assert {"Python", "AiSignX version", "Database", "CPUs", "Memory"} <= {c.name for c in runtime}
    assert all(c.ok and c.info for c in runtime)


def test_selftest_unwritable_folder(app, monkeypatch, tmp_path):
    real = checks._probe_writable

    def probe(path):
        if path.name == "backups":
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(checks, "_probe_writable", probe)
    with app.app_context():
        names = _by_name(checks.run_selftest(live=True))
    assert not names["Backups folder"].ok
    assert "not writable" in names["Backups folder"].detail
    assert names["Backups folder"].fix
    assert names["Uploads folder"].ok


def test_selftest_folder_is_a_file(app, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    app.config["LOG_DIR"] = str(blocker)
    with app.app_context():
        assert not _by_name(checks.run_selftest(live=True))["Logs folder"].ok


def test_selftest_dead_jobs_thread(app):
    app.config["JOBS_ENABLED"] = True
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    app.extensions[jobs.THREAD_KEY] = dead
    with app.app_context():
        row = _by_name(checks.run_selftest(live=True))["Background jobs"]
    assert not row.ok and "not running" in row.detail


def test_selftest_jobs_thread_alive_and_stale(app):
    app.config["JOBS_ENABLED"] = True
    stop = threading.Event()
    thread = threading.Thread(target=stop.wait, daemon=True)
    thread.start()
    try:
        app.extensions[jobs.THREAD_KEY] = thread
        import time
        app.extensions[jobs.LOOP_KEY] = time.time() - 5
        with app.app_context():
            assert _by_name(checks.run_selftest(live=True))["Background jobs"].ok
        app.extensions[jobs.LOOP_KEY] = time.time() - 600
        with app.app_context():
            assert not _by_name(checks.run_selftest(live=True))["Background jobs"].ok
            # From the CLI (another process) the thread is not judged.
            assert _by_name(checks.run_selftest(live=False))["Background jobs"].ok
    finally:
        stop.set()


def test_selftest_jobs_disabled_fails(app):
    with app.app_context():
        row = _by_name(checks.run_selftest(live=True))["Background jobs"]
    assert not row.ok and "AISIGNX_JOBS" in row.detail


def test_selftest_failed_job_and_recent_backup(app):
    with app.app_context():
        Setting.set("job.audit.purge.fail_count", 3)
        Setting.set("job.audit.purge.last_error", "RuntimeError: boom")
        Setting.set_datetime("backup.last_ok_at", utcnow())
        Setting.set("backup.last_name", "aisignx-test.zip")
        db.session.commit()
        names = _by_name(checks.run_selftest(live=True))
    assert not names["Job audit.purge"].ok and "boom" in names["Job audit.purge"].detail
    assert names["Last backup"].ok and "aisignx-test.zip" in names["Last backup"].detail


def test_selftest_ticker_states(app):
    app.config["PLAYER_SCHEDULE_TICKER"] = True
    with app.app_context():
        row = _by_name(checks.run_selftest(live=True))["Schedule ticker"]
    assert row.ok and "not started yet" in row.detail
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    app.extensions["aisignx_schedule_ticker"] = dead
    with app.app_context():
        assert not _by_name(checks.run_selftest(live=True))["Schedule ticker"].ok


def test_selftest_smtp_enabled_without_host_fails(app):
    with app.app_context():
        Setting.set("alerts.email_enabled", "1")
        db.session.commit()
        row = _by_name(checks.run_selftest())["E-mail (SMTP)"]
    assert not row.ok


def test_selftest_check_that_raises_becomes_failure(app, monkeypatch):
    monkeypatch.setattr(checks, "_check_backup", lambda: 1 / 0)
    with app.app_context():
        row = _by_name(checks.run_selftest(live=True))["Last backup"]
    assert not row.ok and "ZeroDivisionError" in row.detail


def test_selftest_schema_mismatch(app, monkeypatch):
    from aisignx import dbinfo

    monkeypatch.setattr(dbinfo, "current_revision", lambda: "0000old")
    with app.app_context():
        row = _by_name(checks.run_selftest(live=True))["Database schema"]
    assert not row.ok and "db upgrade" in row.fix


# --- tail ---------------------------------------------------------------------------------

def test_tail_larger_than_one_block(tmp_path):
    path = tmp_path / "aisignx.log"
    lines = [_line(i) for i in range(5000)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert path.stat().st_size > 2 * BLOCK
    assert tail(path, 200) == lines[-200:]
    assert tail(path, 1000) == lines[-1000:]
    assert tail(path, 5000) == lines[-1000:]  # capped at 1000


def test_tail_reads_rotated_files(tmp_path):
    path = tmp_path / "aisignx.log"
    older = [_line(i) for i in range(100)]
    newer = [_line(i) for i in range(100, 130)]
    (tmp_path / "aisignx.log.1").write_text("\n".join(older) + "\n", encoding="utf-8")
    path.write_text("\n".join(newer) + "\n", encoding="utf-8")
    assert tail(path, 50) == older[-20:] + newer
    # Only the rotated file left (e.g. right after a rollover with nothing written yet).
    path.unlink()
    assert tail(path, 10) == older[-10:]


def test_tail_level_filter_keeps_tracebacks(tmp_path):
    path = tmp_path / "aisignx.log"
    content = [
        _line(1, "INFO"), _line(2, "WARNING"), _line(3, "ERROR"),
        "Traceback (most recent call last):", '  File "x.py", line 1', "ValueError: bad",
        _line(4, "DEBUG"), _line(5, "INFO"),
    ]
    path.write_text("\r\n".join(content) + "\r\n", encoding="utf-8")
    assert tail(path, 200, "warning") == content[1:6]
    assert tail(path, 200, "ERROR") == content[2:6]
    assert tail(path, 200) == content
    assert tail(path, 2) == content[-2:]
    assert level_of(content[2]) == "ERROR" and level_of(content[3]) is None


def test_tail_missing_file_and_bad_bytes(tmp_path):
    assert tail(tmp_path / "nothing.log") == []
    path = tmp_path / "aisignx.log"
    path.write_bytes(_line(1).encode() + b"\n\xff\xfe broken\n")
    out = tail(path)
    assert out[0] == _line(1) and "broken" in out[1]


# --- Logs page ----------------------------------------------------------------------------

def _write_log(app, lines):
    path = log_file_path(app)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_logs_page(admin_client, app):
    assert "There is no log file yet" in admin_client.get("/admin/system/logs").get_data(as_text=True)
    _write_log(app, [_line(1, "INFO"), _line(2, "ERROR"), "<script>x</script>"])
    html = admin_client.get("/admin/system/logs").get_data(as_text=True)
    assert "message number 1" in html and "message number 2" in html
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    html = admin_client.get("/admin/system/logs?level=ERROR&lines=500").get_data(as_text=True)
    assert "message number 1" not in html and "message number 2" in html
    html = admin_client.get("/admin/system/logs?lines=7&level=bogus&auto=1").get_data(as_text=True)
    assert "location.reload" in html and "message number 1" in html


def test_logs_download(admin_client, app):
    resp = admin_client.get("/admin/system/logs/download")
    assert resp.status_code == 302
    _write_log(app, [_line(1)])
    resp = admin_client.get("/admin/system/logs/download")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    assert b"message number 1" in resp.data


def test_real_log_lines_are_parsed(tmp_path, monkeypatch):
    """The viewer understands the format logging_setup actually writes."""
    import logging

    from .conftest import make_csrf_app

    app = make_csrf_app(tmp_path, monkeypatch, LOG_TO_FILE=True)
    logging.getLogger("aisignx.test").warning("hello from the test")
    path = log_file_path(app)
    for h in logging.getLogger("aisignx").handlers:
        h.flush()
    out = tail(path, 50, "WARNING")
    assert any("hello from the test" in line for line in out)
    from aisignx.logging_setup import configure_logging

    app.config["LOG_TO_FILE"] = False
    configure_logging(app)  # detach the file handler so tmp_path can be removed


# --- /healthz -----------------------------------------------------------------------------

def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "app": "aisignx", "api": 1}


def test_healthz_db_down(client, monkeypatch):
    def boom(*a, **kw):
        raise RuntimeError("database is gone")

    monkeypatch.setattr(db.session, "execute", boom)
    resp = client.get("/healthz")
    assert resp.status_code == 503
    assert resp.get_json() == {"status": "error", "app": "aisignx", "api": 1}


# --- CLI ----------------------------------------------------------------------------------

def test_cli_check_all_json(app):
    result = app.test_cli_runner().invoke(args=["check", "--all", "--json"])
    data = json.loads(result.output)
    names = {c["name"] for c in data["checks"]}
    assert {"Database", "Database schema", "Last backup", "Python"} <= names
    assert all("duration_ms" in c for c in data["checks"])
    # Tests run with jobs off and without a backup, so the self-test fails.
    assert data["ok"] is False and result.exit_code == 1


def test_cli_check_all_passes_when_healthy(app, monkeypatch):
    monkeypatch.setattr(checks, "run_checks", lambda: [])
    app.config["JOBS_ENABLED"] = True
    with app.app_context():
        Setting.set_datetime("backup.last_ok_at", utcnow())
        db.session.commit()
    result = app.test_cli_runner().invoke(args=["check", "--all"])
    assert result.exit_code == 0, result.output
    assert "INFO" in result.output and "Database" in result.output
