"""Backup archives: create, list, prune and delete, plus the scheduled backup job.

An archive is a ZIP named aisignx-<UTC yyyymmdd-hhmmss>-<source>-<4 hex>.zip in
BACKUP_DIR, holding (in this order):

    db/aisignx.db          a consistent copy of the SQLite database (sqlite3 backup API)
    instance/secret_key    the session signing key, when stored in the instance folder
    instance/command_key   the key that signs device commands (screens pin its public half: without it
                           they refuse reboot, startup and update commands after a restore)
    uploads/media/*        uploaded media files      (only with "include uploads")
    uploads/thumbs/*       their previews            (only with "include uploads")
    manifest.json          format, versions, alembic revision, size and sha256 per file

Archives contain secrets (password hashes, the secret key): keep them private.
The lock, the background runner and its status are per process (run one server).
"""
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sqlite3
import threading
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import current_app

from .. import dbinfo, maintenance
from ..extensions import db
from ..filters import filesize
from ..models import Setting
from ..timeutil import iso_z, to_local, utcnow

log = logging.getLogger(__name__)

NAME_RE = re.compile(
    r"^aisignx-\d{8}-\d{6}-(manual|scheduled|pre-restore|cli|uploaded)-[0-9a-f]{4}\.zip$")
SOURCES = ("manual", "scheduled", "pre-restore", "cli", "uploaded")
FORMAT = 1
MANIFEST = "manifest.json"
DB_MEMBER = "db/aisignx.db"
SECRET_MEMBER = "instance/secret_key"
COMMAND_KEY_MEMBER = "instance/command_key"
UPLOAD_SUBDIRS = ("media", "thumbs")
UPLOAD_MEMBER_RE = re.compile(r"^uploads/(media|thumbs)/([A-Za-z0-9][A-Za-z0-9._-]{0,200})$")
SPACE_FACTOR = 1.2
STALE_AFTER = timedelta(hours=48)
RETRY_AFTER = timedelta(hours=1)
LEFTOVER_AGE_S = 24 * 3600
MAX_MANIFEST = 16 * 1024 * 1024
CHUNK = 1024 * 1024
MAX_ERROR = 500


class BackupError(Exception):
    """A backup could not be made; the message is meant for the admin."""


class BackupBusy(BackupError):
    """Another backup or restore is running in this process."""


_lock = threading.Lock()
_status_lock = threading.Lock()
_status = {"id": 0, "running": False, "op": None, "started_at": None, "finished_at": None,
           "ok": None, "message": None}
_thread: threading.Thread | None = None


# --- Paths and names --------------------------------------------------------------------

def backup_dir() -> Path:
    path = Path(current_app.config["BACKUP_DIR"]).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_name(source: str, now: datetime | None = None) -> str:
    when = (now or utcnow()).astimezone(timezone.utc)
    return f"aisignx-{when:%Y%m%d-%H%M%S}-{source}-{secrets.token_hex(2)}.zip"


def source_of(name: str) -> str | None:
    match = NAME_RE.match(name)
    return match.group(1) if match else None


def resolve_name(name: str) -> Path:
    """The archive called `name` in BACKUP_DIR. FileNotFoundError for anything else."""
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise FileNotFoundError(name)
    path = backup_dir() / name
    if not path.is_file():
        raise FileNotFoundError(name)
    return path


def _remove(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        log.warning("Could not delete %s: %s", path, exc)


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# --- Create -----------------------------------------------------------------------------

def _upload_files() -> list[tuple[str, Path]]:
    """(member name, path) of every file in uploads/media and uploads/thumbs."""
    root = Path(current_app.config["UPLOAD_FOLDER"])
    out = []
    for sub in UPLOAD_SUBDIRS:
        folder = root / sub
        if not folder.is_dir():
            continue
        for path in sorted(folder.iterdir()):
            if path.name.startswith(".") or not path.is_file():
                continue
            member = f"uploads/{sub}/{path.name}"
            if not UPLOAD_MEMBER_RE.match(member):
                log.warning("Backup skips %s: unexpected file name", path)
                continue
            out.append((member, path))
    return out


def _check_space(folder: Path, live: Path | None, uploads) -> None:
    estimate = sum(_size(p) for _m, p in uploads)
    if live is not None:
        estimate += _size(live) + _size(live.with_name(live.name + "-wal"))
    needed = int(estimate * SPACE_FACTOR)
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise BackupError(f"Not enough free space for a backup in {folder}: "
                          f"{filesize(free)} free, about {filesize(needed)} needed.")


def snapshot_sqlite(live: Path, dest: Path) -> None:
    """Consistent copy of a live (WAL) SQLite database, checked with integrity_check."""
    src = sqlite3.connect(str(live), timeout=30)
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
            dst.execute("PRAGMA journal_mode=DELETE")  # one self-contained file
            row = dst.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise BackupError(f"The database copy failed its integrity check: {row}")
        finally:
            dst.close()
    finally:
        src.close()


def _add_file(zf: zipfile.ZipFile, member: str, path: Path, compress: int) -> dict:
    """Copy path into the archive, hashing as it goes. Returns {size, sha256}."""
    info = zipfile.ZipInfo.from_file(path, member)
    info.compress_type = compress
    digest = hashlib.sha256()
    size = 0
    with open(path, "rb") as src, zf.open(info, "w") as out:
        for chunk in iter(lambda: src.read(CHUNK), b""):
            digest.update(chunk)
            out.write(chunk)
            size += len(chunk)
    return {"size": size, "sha256": digest.hexdigest()}


def create(source: str = "manual", include_uploads: bool | None = None) -> Path:
    """Write a new archive to BACKUP_DIR and return its path.

    Raises BackupBusy when another backup runs, BackupError (or an OSError) on failure;
    a failure is recorded in backup.last_error / last_error_at.
    """
    if source not in SOURCES or source == "uploaded":
        raise ValueError(f"unknown backup source {source!r}")
    if not _lock.acquire(blocking=False):
        raise BackupBusy("Another backup is already running. Try again in a moment.")
    try:
        return _create_locked(source, include_uploads)
    finally:
        _lock.release()


def _create_locked(source: str, include_uploads: bool | None) -> Path:
    if include_uploads is None:
        include_uploads = Setting.get_bool("backup.include_uploads")
    folder = backup_dir()
    name = new_name(source)
    part = folder / (name + ".part")
    snap = folder / f".snapshot-{secrets.token_hex(4)}.db"
    started = time.monotonic()
    try:
        dialect = dbinfo.dialect()
        live = dbinfo.sqlite_path() if dialect == "sqlite" else None
        uploads = _upload_files() if include_uploads else []
        _check_space(folder, live, uploads)
        revision = dbinfo.current_revision()
        files: dict[str, dict] = {}
        with zipfile.ZipFile(part, "w", allowZip64=True) as zf:
            if live is not None:
                snapshot_sqlite(live, snap)
                files[DB_MEMBER] = _add_file(zf, DB_MEMBER, snap, zipfile.ZIP_DEFLATED)
            secret = Path(current_app.instance_path) / "secret_key"
            if secret.is_file():
                files[SECRET_MEMBER] = _add_file(zf, SECRET_MEMBER, secret, zipfile.ZIP_DEFLATED)
            command_key = Path(current_app.instance_path) / "command_key"
            if command_key.is_file():
                files[COMMAND_KEY_MEMBER] = _add_file(zf, COMMAND_KEY_MEMBER, command_key, zipfile.ZIP_DEFLATED)
            for member, path in uploads:
                try:
                    files[member] = _add_file(zf, member, path, zipfile.ZIP_STORED)
                except FileNotFoundError:  # deleted while the backup ran
                    continue
            from .. import __version__

            manifest = {
                "format": FORMAT,
                "app_version": __version__,
                "alembic_revision": revision,
                "db_dialect": dialect,
                "db_included": live is not None,
                "created_at": iso_z(utcnow()),
                "source": source,
                "include_uploads": bool(include_uploads),
                "files": files,
                "totals": {"files": len(files),
                           "bytes": sum(f["size"] for f in files.values())},
            }
            zf.writestr(MANIFEST, json.dumps(manifest, indent=1),
                        compress_type=zipfile.ZIP_DEFLATED)
        final = folder / name
        os.replace(part, final)
    except Exception as exc:
        _remove(part)
        _record_failure(exc)
        raise
    finally:
        _remove(snap)
    _record_success(final)
    log.info("Backup %s written (%s, %.1f s)", name, filesize(_size(final)),
             time.monotonic() - started)
    return final


def _record_success(path: Path) -> None:
    try:
        Setting.set_datetime("backup.last_ok_at", utcnow())
        Setting.set("backup.last_size", _size(path))
        Setting.set("backup.last_name", path.name)
        Setting.set("backup.last_error", "")
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Could not record the backup result")


def _record_failure(exc: Exception) -> None:
    log.error("Backup failed: %s", exc)
    try:
        db.session.rollback()
        Setting.set("backup.last_error", (str(exc) or type(exc).__name__)[:MAX_ERROR])
        Setting.set_datetime("backup.last_error_at", utcnow())
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Could not record the backup failure")


# --- List, delete, prune ----------------------------------------------------------------

def read_manifest(path: Path) -> dict:
    """The manifest of an archive (not verified). Raises BackupError if unreadable."""
    try:
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo(MANIFEST)
            if info.file_size > MAX_MANIFEST:
                raise BackupError("manifest.json is too large")
            data = json.loads(zf.read(info).decode("utf-8"))
    except KeyError:
        raise BackupError("no manifest.json") from None
    except (zipfile.BadZipFile, OSError, ValueError, UnicodeDecodeError) as exc:
        raise BackupError(str(exc) or type(exc).__name__) from exc
    if not isinstance(data, dict):
        raise BackupError("manifest.json is not an object")
    return data


def _parse_time(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def list_backups() -> list[dict]:
    """Every archive in BACKUP_DIR, newest first. A damaged one carries `error`."""
    out = []
    for path in backup_dir().glob("aisignx-*.zip"):
        if not NAME_RE.match(path.name) or not path.is_file():
            continue
        stat = path.stat()
        item = {"name": path.name, "path": path, "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                "source": source_of(path.name), "error": None, "created_at": None,
                "origin": None, "app_version": None, "revision": None,
                "include_uploads": None, "db_included": None, "files": None}
        try:
            manifest = read_manifest(path)
            totals = manifest.get("totals") or {}
            item.update(created_at=_parse_time(manifest.get("created_at")),
                        origin=manifest.get("source"),
                        app_version=manifest.get("app_version"),
                        revision=manifest.get("alembic_revision"),
                        include_uploads=bool(manifest.get("include_uploads")),
                        db_included=DB_MEMBER in (manifest.get("files") or {}),
                        files=totals.get("files"))
        except BackupError as exc:
            item["error"] = f"Damaged archive: {exc}"
        out.append(item)
    out.sort(key=lambda i: i["created_at"] or i["mtime"], reverse=True)
    return out


def delete(name: str) -> None:
    resolve_name(name).unlink()


def prune(keep: int) -> list[str]:
    """Delete scheduled archives beyond the newest `keep`. Others are never pruned."""
    keep = max(1, int(keep))
    scheduled = sorted((p for p in backup_dir().glob("aisignx-*.zip")
                        if source_of(p.name) == "scheduled"), key=lambda p: p.name, reverse=True)
    deleted = []
    for path in scheduled[keep:]:
        try:
            path.unlink()
            deleted.append(path.name)
        except OSError as exc:
            log.warning("Could not prune %s: %s", path.name, exc)
    return deleted


def cleanup_leftovers(max_age_s: int = LEFTOVER_AGE_S) -> int:
    """Remove staging folders, .part files and snapshots left by a crash."""
    cutoff = time.time() - max_age_s
    removed = 0
    for path in backup_dir().iterdir():
        name = path.name
        if not (name.startswith(".staging-") or name.startswith(".snapshot-")
                or name.endswith(".part")):
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            removed += 1
        except OSError as exc:
            log.warning("Could not remove leftover %s: %s", path, exc)
    return removed


# --- Background runs (web) ----------------------------------------------------------------

def status() -> dict:
    """The last background create/restore: {id, running, op, started_at, finished_at,
    ok, message}. In memory, this process only."""
    with _status_lock:
        return dict(_status)


def start_in_background(op: str, fn) -> threading.Thread:
    """Run fn() (returning a message) in a thread with an app context.
    Raises BackupBusy when a run is already going."""
    global _thread
    app = current_app._get_current_object()
    with _status_lock:
        if _status["running"]:
            raise BackupBusy(f"A {_status['op']} is already running.")
        _status.update(id=_status["id"] + 1, running=True, op=op, started_at=utcnow(),
                       finished_at=None, ok=None, message=None)

    def run():
        ok, message = False, None
        try:
            with app.app_context():
                try:
                    message = fn()
                    ok = True
                except Exception as exc:
                    log.exception("Background %s failed", op)
                    message = str(exc) or type(exc).__name__
                finally:
                    db.session.remove()
        finally:
            with _status_lock:
                _status.update(running=False, finished_at=utcnow(), ok=ok, message=message)

    thread = threading.Thread(target=run, name=f"aisignx-backup-{op}", daemon=True)
    _thread = thread
    thread.start()
    return thread


def wait(timeout: float | None = None) -> None:
    """Block until the last background run ends (tests, CLI)."""
    if _thread is not None:
        _thread.join(timeout)


def audit_as(action: str, actor, via: str, *, target=None, detail=None) -> None:
    """Commit an audit row from a background thread on behalf of `actor`
    ((user id, username) or None), with `via` set explicitly."""
    from ..audit import service as audit
    from ..models import AuditLog, User

    try:
        user = None
        if actor:
            uid, uname = actor
            found = db.session.get(User, uid) if uid else None
            # The database may have been replaced (restore): never point at another user.
            user = found if found is not None and found.username == uname else \
                SimpleNamespace(id=None, username=uname)
        audit.record(action, target, detail=detail, user=user)
        for obj in db.session.new:
            if isinstance(obj, AuditLog) and obj.action == action:
                obj.via = via
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Could not save audit action %s", action)


# --- Scheduled job ------------------------------------------------------------------------

def scheduled_job() -> str:
    """Job: once a day, at or after backup.hour (organization time), write a backup."""
    cleanup_leftovers()
    if not Setting.get_bool("backup.enabled"):
        return "scheduled backups are off"
    if maintenance.active():
        return "skipped: maintenance"
    now = utcnow()
    local = to_local(now)
    if local.hour < Setting.get_int("backup.hour", lo=0, hi=23):
        return "not yet"
    today = local.date().isoformat()
    if Setting.get("backup.last_scheduled_date") == today:
        return "already done today"
    retry_after = Setting.get_datetime("backup.retry_after")
    if retry_after is not None and now < retry_after:
        return "waiting to retry"
    try:
        path = create("scheduled")
    except BackupBusy:
        return "another backup is running; trying again later"
    except Exception:
        try:
            db.session.rollback()
            Setting.set_datetime("backup.retry_after", now + RETRY_AFTER)
            db.session.commit()
        except Exception:
            db.session.rollback()
        raise
    Setting.set("backup.last_scheduled_date", today)
    Setting.set("backup.retry_after", None)
    db.session.commit()
    pruned = prune(Setting.get_int("backup.keep", lo=1, hi=100))
    return f"wrote {path.name}; pruned {len(pruned)}"


def is_stale(backups: list[dict] | None = None) -> bool:
    """True when scheduling is on and the newest backup is older than 48 h."""
    if not Setting.get_bool("backup.enabled"):
        return False
    items = list_backups() if backups is None else backups
    times = [i["created_at"] or i["mtime"] for i in items if not i["error"]]
    return bool(times) and utcnow() - max(times) > STALE_AFTER
