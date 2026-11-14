"""Backup and restore (Phase 6, WP4)."""
import errno
import hashlib
import io
import json
import os
import threading
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from aisignx import maintenance
from aisignx.backup import restore as restore_mod
from aisignx.backup import service
from aisignx.extensions import db
from aisignx.models import AuditLog, Media, Setting, User
from .conftest import PASSWORD, login, make_user, png_bytes, run_job, upload


@pytest.fixture(autouse=True)
def _reset_state():
    service.wait(10)
    with service._status_lock:
        service._status.update(running=False, op=None, started_at=None, finished_at=None,
                               ok=None, message=None)
    maintenance.end()
    yield
    service.wait(10)
    maintenance.end()


def uploads_root(app) -> Path:
    return Path(app.config["UPLOAD_FOLDER"])


def media_files(app) -> set[str]:
    folder = uploads_root(app) / "media"
    return {p.name for p in folder.iterdir()} if folder.is_dir() else set()


def make_backup(app, source="manual", include_uploads=True) -> Path:
    with app.app_context():
        return service.create(source, include_uploads=include_uploads)


def rewrite(src: Path, dst: Path, *, manifest=None, extra=None, replace=None,
            rehash=True) -> Path:
    """Copy an archive, changing the manifest and members (hashes recomputed)."""
    with zipfile.ZipFile(src) as zin:
        members = {n: zin.read(n) for n in zin.namelist() if n != "manifest.json"}
        data = json.loads(zin.read("manifest.json"))
    members.update(replace or {})
    members.update(extra or {})
    if rehash:
        data["files"] = {n: {"size": len(b), "sha256": hashlib.sha256(b).hexdigest()}
                         for n, b in members.items()}
        data["totals"] = {"files": len(members), "bytes": sum(len(b) for b in members.values())}
    data.update(manifest or {})
    with zipfile.ZipFile(dst, "w") as zout:
        for name, content in members.items():
            zout.writestr(name, content)
        zout.writestr("manifest.json", json.dumps(data))
    return dst


# --- Create -----------------------------------------------------------------------------

def test_create_writes_manifest_with_checksums(admin_client, app):
    upload(admin_client, ("a.png", png_bytes()))
    path = make_backup(app)
    assert service.NAME_RE.match(path.name) and "-manual-" in path.name
    assert not list(path.parent.glob("*.part")) and not list(path.parent.glob(".snapshot-*"))
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
        assert names[0] == "db/aisignx.db" and names[-1] == "manifest.json"
        assert manifest["format"] == 1 and manifest["source"] == "manual"
        assert manifest["db_dialect"] == "sqlite" and manifest["db_included"] is True
        assert manifest["include_uploads"] is True
        assert any(n.startswith("uploads/media/") for n in names)
        assert any(n.startswith("uploads/thumbs/") for n in names)
        for name, entry in manifest["files"].items():
            content = zf.read(name)
            assert entry["size"] == len(content)
            assert entry["sha256"] == hashlib.sha256(content).hexdigest()
        assert manifest["totals"]["files"] == len(names) - 1
        assert zf.getinfo(next(n for n in names if n.startswith("uploads/"))).compress_type \
            == zipfile.ZIP_STORED
    with app.app_context():
        from aisignx import dbinfo

        assert manifest["alembic_revision"] == dbinfo.head_revision()
        assert Setting.get("backup.last_name") == path.name
        assert Setting.get_datetime("backup.last_ok_at") is not None
        assert restore_mod.validate(path)["db_included"] is True


def test_create_without_uploads_and_busy(app):
    path = make_backup(app, include_uploads=False)
    with zipfile.ZipFile(path) as zf:
        assert not [n for n in zf.namelist() if n.startswith("uploads/")]
    service._lock.acquire()
    try:
        with pytest.raises(service.BackupBusy):
            make_backup(app)
    finally:
        service._lock.release()


def test_create_failure_records_error_and_leaves_no_part(app, monkeypatch):
    monkeypatch.setattr(service.shutil, "disk_usage",
                        lambda p: type("U", (), {"free": 0, "total": 1, "used": 1})())
    upload_dir = uploads_root(app) / "media"
    upload_dir.mkdir(parents=True)
    (upload_dir / "abc.png").write_bytes(b"x" * 1000)
    with pytest.raises(service.BackupError, match="Not enough free space"):
        make_backup(app)
    with app.app_context():
        assert "free space" in Setting.get("backup.last_error")
        assert Setting.get_datetime("backup.last_error_at") is not None
        assert not list(service.backup_dir().iterdir())


# --- Restore ----------------------------------------------------------------------------

def test_round_trip_restores_data_and_uploads(admin_client, app):
    upload(admin_client, ("a.png", png_bytes()))
    make_user(app, "kept", "editor")
    before_files = media_files(app)
    path = make_backup(app)

    # Change everything after the backup.
    with app.app_context():
        db.session.delete(User.query.filter_by(username="kept").one())
        Media.query.one().name = "renamed"
        db.session.commit()
    make_user(app, "added", "viewer")
    extra = uploads_root(app) / "media" / "extra.png"
    extra.write_bytes(png_bytes())
    lost = uploads_root(app) / "media" / sorted(before_files)[0]
    lost.unlink()

    with app.app_context():
        result = restore_mod.restore(path, actor_via="cli")
    assert result["skipped"] == [] and result["pre_restore"]
    assert media_files(app) == before_files and not extra.exists() and lost.exists()
    with app.app_context():
        assert {u.username for u in User.query} == {"admin", "kept"}
        assert Media.query.one().name == "a"
        row = AuditLog.query.filter_by(action="backup.restore").one()
        assert row.via == "cli" and row.target_name == path.name
        assert service.source_of(result["pre_restore"]) == "pre-restore"
        assert (service.backup_dir() / result["pre_restore"]).is_file()
        assert not list(service.backup_dir().glob(".staging-*"))
        # The restored database keeps this server's backup status (the safety backup).
        assert Setting.get("backup.last_name") == result["pre_restore"]
    assert maintenance.active() is None
    # The app keeps working on the restored database.
    login(admin_client, "admin")
    assert admin_client.get("/media/").status_code == 200


def test_restore_writes_secret_key_only_without_env(app, monkeypatch):
    path = make_backup(app, include_uploads=False)
    secret = Path(app.instance_path) / "secret_key"
    with zipfile.ZipFile(path) as zf:
        has_secret = "instance/secret_key" in zf.namelist()
    if not has_secret:  # instance folder had no key file: give the archive one
        path = rewrite(path, path.with_name("aisignx-20260101-000000-cli-abcd.zip"),
                       extra={"instance/secret_key": b"restored-key"})
    original = secret.read_bytes() if secret.exists() else None
    with app.app_context():
        restore_mod.restore(path, skip_snapshot=True)  # env key is set: file untouched
    assert (secret.read_bytes() if secret.exists() else None) == original
    monkeypatch.delenv("AISIGNX_SECRET_KEY")
    with zipfile.ZipFile(path) as zf:
        expected = zf.read("instance/secret_key")
    with app.app_context():
        restore_mod.restore(path, skip_snapshot=True)
    assert secret.read_bytes() == expected


@pytest.mark.parametrize("case", ["zip-slip", "backslash", "unknown", "bad-sha", "newer",
                                  "missing-member", "format", "not-zip"])
def test_validate_refuses_bad_archives(app, tmp_path, case):
    good = make_backup(app, include_uploads=False)
    bad = tmp_path / "bad.zip"
    if case == "zip-slip":
        rewrite(good, bad, extra={"uploads/media/../../evil.txt": b"x"})
    elif case == "backslash":
        rewrite(good, bad, extra={"uploads\\media\\x.png": b"x"})
    elif case == "unknown":
        rewrite(good, bad, extra={"config.py": b"x"})
    elif case == "bad-sha":
        rewrite(good, bad)
        data = json.loads(zipfile.ZipFile(bad).read("manifest.json"))
        data["files"]["db/aisignx.db"]["sha256"] = "0" * 64
        rewrite(bad, tmp_path / "bad2.zip", manifest={"files": data["files"]}, rehash=False)
        bad = tmp_path / "bad2.zip"
    elif case == "newer":
        rewrite(good, bad, manifest={"alembic_revision": "ffffffffffff"})
    elif case == "missing-member":
        rewrite(good, bad, manifest={"files": {"db/aisignx.db": {"size": 1, "sha256": "x"},
                                               "uploads/media/gone.png": {"size": 1,
                                                                          "sha256": "x"}}},
                rehash=False)
    elif case == "format":
        rewrite(good, bad, manifest={"format": 2})
    else:
        bad.write_bytes(b"not a zip at all")
    with app.app_context():
        with pytest.raises(restore_mod.RestoreError) as err:
            restore_mod.restore(bad)
    if case == "newer":
        assert str(err.value) == "This backup is from a newer AiSignX. Upgrade first."
    assert not (tmp_path / "evil.txt").exists()
    assert maintenance.active() is None
    with app.app_context():  # no safety backup was taken: nothing started
        assert not [b for b in service.list_backups() if b["source"] == "pre-restore"]


def test_failing_safety_backup_aborts_restore(admin_client, app, monkeypatch):
    path = make_backup(app)
    make_user(app, "after", "viewer")

    def boom(*a, **kw):
        raise service.BackupError("disk full")

    monkeypatch.setattr(service, "create", boom)
    with app.app_context():
        with pytest.raises(restore_mod.RestoreError, match="Nothing was changed"):
            restore_mod.restore(path)
        assert User.query.filter_by(username="after").count() == 1
    assert maintenance.active() is None


def test_failure_after_db_write_puts_safety_backup_back(admin_client, app, monkeypatch):
    path = make_backup(app)
    make_user(app, "after", "viewer")
    real = restore_mod._sync_uploads
    calls = []

    def flaky(zf, files):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("simulated failure")
        return real(zf, files)

    monkeypatch.setattr(restore_mod, "_sync_uploads", flaky)
    with app.app_context():
        with pytest.raises(restore_mod.RestoreError, match="previous data was put back"):
            restore_mod.restore(path)
    with app.app_context():
        assert User.query.filter_by(username="after").count() == 1
    assert maintenance.active() is None


def test_restore_signs_out_every_session_even_when_ids_are_reused(configured, app):
    alice = make_user(app, "alice", "admin")
    path = make_backup(app, include_uploads=False)
    with app.app_context():
        db.session.delete(db.session.get(User, alice))
        db.session.commit()
    assert make_user(app, "carol", "viewer") == alice  # SQLite hands out the same id
    carol, remembered = app.test_client(), app.test_client()
    login(carol, "carol")
    remembered.post("/login", data={"username": "carol", "password": PASSWORD,
                                    "remember": "y"})
    cookie = remembered.get_cookie("remember_token")
    assert cookie is not None
    remember_only = app.test_client()
    remember_only.set_cookie("remember_token", cookie.value)
    assert carol.get("/admin/backups/").status_code == 403
    assert remember_only.get("/admin/backups/").status_code == 403  # signed in as carol

    with app.app_context():
        restore_mod.restore(path, actor_via="cli", skip_snapshot=True)
    # Without the session epoch both would now load alice (an admin) from the old id.
    for c in (carol, remember_only):
        resp = c.get("/admin/backups/")
        assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_sessions_from_before_the_epoch_format_do_not_load(app, configured):
    from aisignx.models.user import load_user, session_epoch

    with app.app_context():
        uid = User.query.filter_by(username="admin").one().id
        assert load_user(str(uid)) is None  # a bare id
        assert load_user(f"{uid}:stale") is None
        assert load_user(f"{uid}:{session_epoch()}").id == uid


def test_db_only_restore_takes_a_safety_backup_with_uploads(admin_client, app):
    upload(admin_client, ("a.png", png_bytes()))
    path = make_backup(app, include_uploads=False)
    upload(admin_client, ("b.png", png_bytes(color=(1, 2, 3))))  # after the backup
    files = media_files(app)
    with app.app_context():
        result = restore_mod.restore(path, actor_via="cli")
        pre = service.backup_dir() / result["pre_restore"]
    with zipfile.ZipFile(pre) as zf:
        assert {n.rsplit("/", 1)[1] for n in zf.namelist()
                if n.startswith("uploads/media/")} == files
    # The restored database knows only a.png; the orphan job keeps b.png after a restore.
    for name in files:
        old = datetime.now().timestamp() - 7200
        os.utime(uploads_root(app) / "media" / name, (old, old))
    assert "media kept" in run_job(app, "media.orphans")
    assert media_files(app) == files
    with app.app_context():  # a day later the job cleans up again
        Setting.set_datetime(restore_mod.RESTORED_AT_KEY,
                             datetime.now(timezone.utc) - timedelta(hours=25))
        db.session.commit()
    run_job(app, "media.orphans")
    assert len(media_files(app)) == 1
    # Undoing the restore with the safety backup brings b.png back.
    with app.app_context():
        restore_mod.restore(pre, actor_via="cli", skip_snapshot=True)
        assert Media.query.count() == 2
    assert media_files(app) == files


def test_disk_full_while_writing_uploads_rolls_back_and_deletes_nothing(admin_client, app,
                                                                        monkeypatch):
    upload(admin_client, ("a.png", png_bytes()))
    path = make_backup(app)
    make_user(app, "after", "viewer")
    extra = uploads_root(app) / "media" / "extra.png"
    extra.write_bytes(png_bytes())
    (uploads_root(app) / "media" / sorted(media_files(app) - {"extra.png"})[0]).unlink()
    real, calls = restore_mod.os.replace, []

    def full(src, dst):
        if Path(dst).parent.name == "media" and not calls:
            calls.append(dst)
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(src, dst)

    monkeypatch.setattr(restore_mod.os, "replace", full)
    with app.app_context():
        with pytest.raises(restore_mod.RestoreError, match="previous data was put back"):
            restore_mod.restore(path)
        assert User.query.filter_by(username="after").count() == 1
    assert calls and extra.exists()


def test_locked_upload_is_reported_and_extra_files_are_kept(admin_client, app, monkeypatch):
    upload(admin_client, ("a.png", png_bytes()))
    path = make_backup(app)
    extra = uploads_root(app) / "media" / "extra.png"
    extra.write_bytes(png_bytes())
    target = uploads_root(app) / "media" / sorted(media_files(app) - {"extra.png"})[0]
    target.write_bytes(b"changed")
    real = restore_mod.os.replace

    def locked(src, dst):
        if Path(dst) == target:
            raise PermissionError(13, "The process cannot access the file")
        return real(src, dst)

    monkeypatch.setattr(restore_mod.os, "replace", locked)
    with app.app_context():
        result = restore_mod.restore(path, skip_snapshot=True)
    assert len(result["skipped"]) == 1 and target.name in result["skipped"][0]
    assert extra.exists()


def test_restore_refuses_when_the_uploads_volume_is_full(admin_client, app, monkeypatch):
    upload(admin_client, ("a.png", png_bytes()))
    path = make_backup(app)
    make_user(app, "after", "viewer")
    for p in (uploads_root(app) / "media").iterdir():
        p.unlink()
    monkeypatch.setattr(restore_mod, "_free", lambda folder: 10)
    with app.app_context():
        with pytest.raises(restore_mod.RestoreError, match="not enough free disk space"):
            restore_mod.restore(path)
        assert User.query.filter_by(username="after").count() == 1
    assert maintenance.active() is None


def test_no_job_runs_in_maintenance(app, monkeypatch):
    from aisignx import jobs

    monkeypatch.setattr(jobs, "JOBS", (jobs.Job("t.noop", 60, "aisignx.jobs:status"),))
    maintenance.begin("restore")
    try:
        assert jobs.run_due(app) == []
    finally:
        maintenance.end()
    assert jobs.run_due(app) == ["t.noop"]


# --- Prune and schedule ----------------------------------------------------------------

def test_prune_only_deletes_scheduled(app):
    with app.app_context():
        folder = service.backup_dir()
        for i in range(4):
            (folder / f"aisignx-2026010{i + 1}-030000-scheduled-000{i}.zip").write_bytes(b"x")
        (folder / "aisignx-20250101-030000-manual-0000.zip").write_bytes(b"x")
        (folder / "aisignx-20250101-030000-pre-restore-0000.zip").write_bytes(b"x")
        deleted = service.prune(2)
        assert sorted(deleted) == ["aisignx-20260101-030000-scheduled-0000.zip",
                                   "aisignx-20260102-030000-scheduled-0001.zip"]
        left = {p.name for p in folder.iterdir()}
        assert "aisignx-20250101-030000-manual-0000.zip" in left
        assert "aisignx-20250101-030000-pre-restore-0000.zip" in left
        assert len(left) == 4


def _at(monkeypatch, hour):
    now = datetime(2026, 11, 12, hour, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(service, "utcnow", lambda: now)
    return now


def test_scheduled_job_respects_hour_and_runs_once_a_day(app, monkeypatch):
    with app.app_context():
        Setting.set("backup.hour", "3")
        db.session.commit()
    _at(monkeypatch, 2)
    assert run_job(app, "backup.scheduled") == "not yet"
    _at(monkeypatch, 4)
    assert run_job(app, "backup.scheduled").startswith("wrote aisignx-")
    assert run_job(app, "backup.scheduled") == "already done today"
    with app.app_context():
        assert Setting.get("backup.last_scheduled_date") == "2026-11-12"
        assert [b["source"] for b in service.list_backups()] == ["scheduled"]
        Setting.set("backup.enabled", "0")
        db.session.commit()
    assert run_job(app, "backup.scheduled") == "scheduled backups are off"


def test_scheduled_job_failure_sets_error_and_retry(app, monkeypatch):
    now = _at(monkeypatch, 5)

    def boom(*a, **kw):
        raise OSError("no space")

    monkeypatch.setattr(service, "snapshot_sqlite", boom)
    with pytest.raises(OSError):
        run_job(app, "backup.scheduled")
    with app.app_context():
        assert Setting.get("backup.last_error") == "no space"
        assert Setting.get_datetime("backup.retry_after") == now + timedelta(hours=1)
        assert Setting.get("job.backup.scheduled.fail_count") == "1"
    assert run_job(app, "backup.scheduled") == "waiting to retry"


def test_cleanup_removes_old_staging(app):
    import os
    import time

    with app.app_context():
        old = service.backup_dir() / ".staging-20200101-000000-abcd"
        old.mkdir()
        fresh = service.backup_dir() / "x.zip.part"
        fresh.write_bytes(b"x")
        past = time.time() - 3 * 86400
        os.utime(old, (past, past))
        assert service.cleanup_leftovers() == 1
        assert not old.exists() and fresh.exists()


# --- CLI --------------------------------------------------------------------------------

def test_cli_create_list_verify_restore(configured):
    app = configured
    runner = app.test_cli_runner()
    result = runner.invoke(args=["backup", "create", "--no-uploads"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        name = service.list_backups()[0]["name"]
        assert AuditLog.query.filter_by(action="backup.create", via="cli").count() == 1
    assert "-cli-" in name
    result = runner.invoke(args=["backup", "list"])
    assert name in result.output
    result = runner.invoke(args=["backup", "verify", name])
    assert result.exit_code == 0 and "OK" in result.output
    result = runner.invoke(args=["backup", "verify", "nope.zip"])
    assert result.exit_code != 0
    result = runner.invoke(args=["backup", "restore", name], input="n\n")
    assert result.exit_code != 0 and "Stop the server first" in result.output
    result = runner.invoke(args=["backup", "restore", name, "--yes"])
    assert result.exit_code == 0, result.output
    assert "Restored" in result.output
    result = runner.invoke(args=["backup", "prune"])
    assert result.exit_code == 0


# --- Web ------------------------------------------------------------------------------

def test_editor_and_viewer_get_403(configured, client):
    make_user(configured, "ed", "editor")
    login(client, "ed")
    assert client.get("/admin/backups/").status_code == 403
    assert client.post("/admin/backups/create").status_code == 403
    assert client.get("/admin/backups/status").status_code == 403


def test_index_lists_and_downloads(admin_client, app):
    path = make_backup(app)
    page = admin_client.get("/admin/backups/")
    assert page.status_code == 200 and path.name.encode() in page.data
    resp = admin_client.get(f"/admin/backups/download/{path.name}")
    assert resp.status_code == 200 and resp.data == path.read_bytes()
    resp.close()
    assert admin_client.get("/admin/backups/download/../x").status_code == 404
    assert admin_client.get("/admin/backups/download/..%2Fx").status_code == 404
    assert admin_client.get("/admin/backups/download/aisignx.db").status_code == 404
    with app.app_context():
        assert AuditLog.query.filter_by(action="backup.download").count() == 1


def test_create_in_background_and_status(admin_client, app):
    resp = admin_client.post("/admin/backups/create", data={"include_uploads": "y"})
    assert resp.status_code == 303
    service.wait(30)
    data = admin_client.get("/admin/backups/status").get_json()
    assert data["running"] is False and data["ok"] is True and data["op"] == "create"
    with app.app_context():
        row = AuditLog.query.filter_by(action="backup.create").one()
        assert row.via == "web" and row.username == "admin"


def test_upload_accepts_valid_and_rejects_bad(admin_client, app, tmp_path):
    good = make_backup(app, include_uploads=False)
    content = good.read_bytes()
    good.unlink()
    resp = admin_client.post("/admin/backups/upload",
                             data={"file": (io.BytesIO(content), "mine.zip")},
                             content_type="multipart/form-data", follow_redirects=True)
    assert b"Backup uploaded" in resp.data
    with app.app_context():
        assert [b["source"] for b in service.list_backups()] == ["uploaded"]
    resp = admin_client.post("/admin/backups/upload",
                             data={"file": (io.BytesIO(b"hello"), "x.zip")},
                             content_type="multipart/form-data", follow_redirects=True)
    assert b"Upload rejected" in resp.data
    source = tmp_path / "src.zip"
    source.write_bytes(content)
    bad = rewrite(source, tmp_path / "bad.zip", manifest={"format": 99})
    resp = admin_client.post("/admin/backups/upload",
                             data={"file": (io.BytesIO(bad.read_bytes()), "bad.zip")},
                             content_type="multipart/form-data", follow_redirects=True)
    assert b"Upload rejected" in resp.data
    with app.app_context():
        assert len(service.list_backups()) == 1
        assert not list(service.backup_dir().glob("*.part"))


def test_restore_page_requires_typed_name(admin_client, app):
    path = make_backup(app, include_uploads=False)
    url = f"/admin/backups/restore/{path.name}"
    assert admin_client.get(url).status_code == 200
    resp = admin_client.post(url, data={"confirm": "wrong"})
    assert b"Type the exact file name" in resp.data
    assert service.status()["running"] is False


def test_maintenance_during_web_restore_then_sign_out(admin_client, app, monkeypatch):
    path = make_backup(app, include_uploads=False)
    entered, release = threading.Event(), threading.Event()
    real = restore_mod.write_db

    def paused(staged, live):
        entered.set()
        assert release.wait(30)
        return real(staged, live)

    monkeypatch.setattr(restore_mod, "write_db", paused)
    resp = admin_client.post(f"/admin/backups/restore/{path.name}", data={"confirm": path.name})
    assert resp.status_code == 200 and b"backup-progress" in resp.data
    assert entered.wait(30)
    try:
        assert admin_client.get("/media/").status_code == 503
        assert admin_client.get("/healthz").status_code == 200
        busy = admin_client.get("/admin/backups/status")
        assert busy.status_code == 200 and busy.get_json()["running"] is True
    finally:
        release.set()
    service.wait(30)
    done = admin_client.get("/admin/backups/status").get_json()
    assert done["signed_out"] is True and "sign in again" in done["message"]
    assert admin_client.get("/admin/backups/").status_code == 302  # signed out
    with app.app_context():
        row = AuditLog.query.filter_by(action="backup.restore").one()
        assert row.via == "web" and row.username == "admin"


def test_delete_and_settings(admin_client, app):
    path = make_backup(app, include_uploads=False)
    resp = admin_client.post(f"/admin/backups/delete/{path.name}")
    assert resp.status_code == 303 and not path.exists()
    assert admin_client.post("/admin/backups/delete/../x").status_code == 404
    resp = admin_client.post("/admin/backups/settings",
                             data={"enabled": "y", "hour": "5", "keep": "3"})
    assert resp.status_code == 303
    with app.app_context():
        assert Setting.get("backup.hour") == "5" and Setting.get("backup.keep") == "3"
        assert Setting.get_bool("backup.include_uploads") is False
        row = AuditLog.query.filter_by(action="backup.settings").one()
        assert set(json.loads(row.detail)["changed"]) == {"backup.hour", "backup.keep",
                                                          "backup.include_uploads"}
    admin_client.post("/admin/backups/settings", data={"enabled": "y", "hour": "25", "keep": "3"})
    with app.app_context():
        assert Setting.get("backup.hour") == "5"


def test_stale_banner(admin_client, app, monkeypatch):
    make_backup(app, include_uploads=False)
    assert b"more than 48 hours old" not in admin_client.get("/admin/backups/").data
    later = datetime.now(timezone.utc) + timedelta(days=3)
    monkeypatch.setattr(service, "utcnow", lambda: later)
    assert b"more than 48 hours old" in admin_client.get("/admin/backups/").data
