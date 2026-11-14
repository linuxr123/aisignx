"""Verify and restore backup archives.

restore() runs with the site in maintenance mode (503 for everyone, this process only).
It first writes a "pre-restore" safety backup and refuses to go on if that fails. The
database is written with the SQLite backup API into the live file (WAL-safe, no file
rename, so it also works on Windows while the server runs). If any later step fails,
the safety backup is put back automatically.

The safety backup always includes the uploads, so it can undo a database-only restore
even after the media.orphans job has removed files the restored database does not know.
A restore of a database signs everyone out (models/user.py, session epoch).
"""
import hashlib
import logging
import os
import secrets
import shutil
import sqlite3
import zipfile
import zlib
from pathlib import Path

from flask import current_app

from .. import dbinfo, maintenance
from ..extensions import db
from ..timeutil import utcnow
from . import service
from .service import (CHUNK, DB_MEMBER, FORMAT, MANIFEST, SECRET_MEMBER,
                      UPLOAD_MEMBER_RE, UPLOAD_SUBDIRS, BackupError)

log = logging.getLogger(__name__)

NEWER_MESSAGE = "This backup is from a newer AiSignX. Upgrade first."
# Runtime status of this server's backups: kept across a restore, so the restored
# database does not claim an old "last backup" (that would raise a stale alert).
STATUS_KEYS = ("backup.last_ok_at", "backup.last_size", "backup.last_name",
               "backup.last_error", "backup.last_error_at", "backup.last_scheduled_date",
               "backup.retry_after")
# When the last restore finished: the offline alerts and the orphan-file job wait after it.
RESTORED_AT_KEY = "backup.last_restore_at"


class RestoreError(BackupError):
    """The archive cannot be restored; the message is meant for the admin."""


def _unsafe(name: str) -> bool:
    return (not name or ".." in name or "\\" in name or ":" in name or "\x00" in name
            or name.startswith("/"))


def _allowed(name: str) -> bool:
    return name in (MANIFEST, DB_MEMBER, SECRET_MEMBER) or bool(UPLOAD_MEMBER_RE.match(name))


def _inside(root: Path, name: str) -> Path:
    """root/name, refusing anything that resolves outside root."""
    root = root.resolve()
    dest = (root / name).resolve()
    if dest.parent != root:
        raise RestoreError(f"Unsafe path in archive: {name!r}")
    return dest


def _sha256_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with zf.open(info) as src:
        for chunk in iter(lambda: src.read(CHUNK), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _check_entries(files) -> None:
    if not isinstance(files, dict):
        raise RestoreError("The manifest has no file list.")
    for name, entry in files.items():
        if (not isinstance(entry, dict) or not isinstance(entry.get("size"), int)
                or isinstance(entry.get("size"), bool) or entry["size"] < 0
                or not isinstance(entry.get("sha256"), str)):
            raise RestoreError(f"The manifest entry for {name!r} is invalid.")


def validate(path) -> dict:
    """Check an archive completely (names, sizes, sha256, revision). Returns the
    manifest, with `db_included` set from its contents. Raises RestoreError."""
    path = Path(path)
    if not path.is_file() or not zipfile.is_zipfile(path):
        raise RestoreError("This is not a backup archive (not a ZIP file).")
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            names = [i.filename for i in infos]
            if len(set(names)) != len(names):
                raise RestoreError("The archive contains duplicate entries.")
            for name in names:
                if _unsafe(name):
                    raise RestoreError(f"Unsafe path in archive: {name!r}")
                if not _allowed(name):
                    raise RestoreError(f"Unexpected file in archive: {name!r}")
            if MANIFEST not in names:
                raise RestoreError("The archive has no manifest.json; it is not an "
                                   "AiSignX backup.")
            manifest = service.read_manifest(path)
            if manifest.get("format") != FORMAT:
                raise RestoreError(f"Unsupported backup format {manifest.get('format')!r}.")
            files = manifest.get("files")
            _check_entries(files)
            members = {i.filename: i for i in infos if i.filename != MANIFEST}
            if set(members) != set(files):
                diff = sorted(set(members) ^ set(files))
                raise RestoreError("The archive does not match its manifest "
                                   f"({', '.join(diff[:3])}).")
            declared = (manifest.get("totals") or {}).get("bytes")
            total = sum(i.file_size for i in members.values())
            if not isinstance(declared, int) or total > declared:
                raise RestoreError("The archive is larger than its manifest says.")
            for name, info in members.items():
                if info.file_size != files[name]["size"]:
                    raise RestoreError(f"Wrong size for {name}.")
            free = shutil.disk_usage(service.backup_dir()).free
            if total > free:
                raise RestoreError("Not enough free disk space to restore this backup.")
            for name, info in members.items():
                digest, size = _sha256_member(zf, info)
                if digest != files[name]["sha256"] or size != files[name]["size"]:
                    raise RestoreError(f"Checksum mismatch for {name}: the archive is damaged.")
    except RestoreError:
        raise
    except BackupError as exc:
        raise RestoreError(f"The archive is damaged: {exc}") from exc
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, EOFError,
            NotImplementedError, OSError) as exc:
        raise RestoreError(f"The archive is damaged: {exc}") from exc
    manifest["db_included"] = DB_MEMBER in files
    revision = manifest.get("alembic_revision")
    if manifest["db_included"] and not isinstance(revision, str):
        raise RestoreError("The manifest has no database revision.")
    if revision and revision not in dbinfo.known_revisions():
        raise RestoreError(NEWER_MESSAGE)
    return manifest


def _live_db(manifest: dict) -> Path | None:
    """The live SQLite file to overwrite, or None for an uploads-only archive."""
    if not manifest["db_included"]:
        return None
    live = dbinfo.sqlite_path() if dbinfo.dialect() == "sqlite" else None
    if live is None:
        raise RestoreError("This server does not use a SQLite file, so the database in "
                           "this backup cannot be restored here. Use pg_restore; see "
                           "docs/backup.md.")
    return live


def restore(path, *, actor_via: str = "web", actor=None, skip_snapshot: bool = False) -> dict:
    """Restore an archive over the running instance. `actor` is (user id, username)
    of whoever asked (for the audit row). Returns {name, pre_restore, skipped, revision}."""
    path = Path(path)
    manifest = validate(path)
    live = _live_db(manifest)
    maintenance.begin("restore")
    try:
        pre = None
        if not skip_snapshot:
            try:
                pre = service.create("pre-restore", include_uploads=True)
            except Exception as exc:
                raise RestoreError(f"Restore cancelled: the safety backup taken first "
                                   f"failed ({exc}). Nothing was changed.") from exc
        _check_space(manifest, live)
        kept = _read_status()
        try:
            skipped = _apply(path, manifest, live)
        except Exception as exc:
            log.exception("Restore of %s failed", path.name)
            if pre is None:
                raise RestoreError(f"Restore failed: {exc}") from exc
            try:
                _apply(pre, validate(pre), live)
            except Exception as exc2:
                log.exception("Putting back %s failed", pre.name)
                raise RestoreError(
                    f"Restore failed: {exc}. Putting back the safety backup {pre.name} "
                    f"also failed: {exc2}. Stop the server and run "
                    f"`flask --app wsgi backup restore {pre.name}`.") from exc
            raise RestoreError(f"Restore failed: {exc}. The previous data was put back "
                               f"from {pre.name}.") from exc
        _write_status(kept)
        service.audit_as("backup.restore", actor, actor_via, target=("backup", None, path.name),
                         detail={"name": path.name,
                                 "revision": manifest.get("alembic_revision"),
                                 "created_at": manifest.get("created_at"),
                                 "pre_restore": pre.name if pre else None,
                                 "skipped_files": skipped[:50]})
        _notify_screens()
    finally:
        maintenance.end()
    log.info("Restored %s (%d files skipped)", path.name, len(skipped))
    return {"name": path.name, "pre_restore": pre.name if pre else None, "skipped": skipped,
            "revision": manifest.get("alembic_revision")}


def _free(folder: Path) -> int:
    return shutil.disk_usage(folder).free


def _existing(path: Path) -> Path:
    """The path itself or its nearest existing parent."""
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return path


def _check_space(manifest: dict, live: Path | None) -> None:
    """Refuse, before anything is changed, when a volume the restore writes to (the
    backup folder for staging, the database, the uploads) lacks the space it needs.
    Runs after the safety backup, which uses space too."""
    files = manifest["files"]
    need: dict[int, list] = {}  # st_dev -> [bytes, folder]

    def add(folder: Path, size: int) -> None:
        folder = _existing(folder)
        need.setdefault(os.stat(folder).st_dev, [0, folder])[0] += max(size, 0)

    if live is not None:
        size = files[DB_MEMBER]["size"]
        add(service.backup_dir(), size)  # staged copy
        try:
            current = live.stat().st_size
        except OSError:
            current = 0
        add(live.parent, size - current)  # written in place
    if manifest.get("include_uploads"):
        root = Path(current_app.config["UPLOAD_FOLDER"])
        total = largest = 0
        for member, entry in files.items():
            match = UPLOAD_MEMBER_RE.match(member)
            if not match:
                continue
            try:
                current = (root / match.group(1) / match.group(2)).stat().st_size
            except OSError:
                current = 0
            total += max(entry["size"] - current, 0)  # nothing is deleted until the end
            largest = max(largest, entry["size"])  # one temporary copy at a time
        add(root, total + largest)
    for size, folder in need.values():
        if size > _free(folder):
            raise RestoreError(f"Restore cancelled: not enough free disk space in {folder} "
                               f"({size // 2**20 + 1} MB needed). Nothing was changed.")


def _read_status() -> dict:
    from ..models import Setting

    try:
        return {key: Setting.get(key) for key in STATUS_KEYS}
    except Exception:
        db.session.rollback()
        log.exception("Could not read the backup status before the restore")
        return {}


def _write_status(values: dict) -> None:
    from ..models import Setting

    try:
        for key, value in values.items():
            Setting.set(key, value)
        Setting.set_datetime(RESTORED_AT_KEY, utcnow())
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Could not keep the backup status across the restore")


def _apply(path: Path, manifest: dict, live: Path | None) -> list[str]:
    """Write the archive's database, uploads and secret key. Returns skipped files."""
    staging = service.backup_dir() / f".staging-{utcnow():%Y%m%d-%H%M%S}-{secrets.token_hex(2)}"
    staging.mkdir()
    skipped: list[str] = []
    try:
        with zipfile.ZipFile(path) as zf:
            if live is not None:
                staged = staging / "aisignx.db"
                with zf.open(DB_MEMBER) as src, open(staged, "wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
                write_db(staged, live)
            if manifest.get("include_uploads"):
                skipped = _sync_uploads(zf, manifest["files"])
            if SECRET_MEMBER in manifest["files"]:
                _write_secret(zf.read(SECRET_MEMBER))
        if live is not None:
            _upgrade()
            _sign_everyone_out()
        return skipped
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _sign_everyone_out() -> None:
    """Sessions hold a user id, which may name someone else in the restored users table
    (and the restored secret key may be the old one): invalidate them all."""
    from ..models.user import new_session_epoch

    new_session_epoch()
    db.session.commit()


def write_db(staged: Path, live: Path) -> None:
    """Copy a database file into the live database, in place."""
    db.session.remove()
    db.engine.dispose()
    src = sqlite3.connect(str(staged))
    try:
        dst = sqlite3.connect(str(live), timeout=60)
        try:
            src.backup(dst)
            row = dst.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                raise RestoreError(f"The restored database failed its integrity check: {row}")
        finally:
            dst.close()
    finally:
        src.close()
    db.engine.dispose()


def _same_file(dest: Path, entry: dict) -> bool:
    try:
        if not dest.is_file() or dest.stat().st_size != entry["size"]:
            return False
        digest = hashlib.sha256()
        with open(dest, "rb") as fh:
            for chunk in iter(lambda: fh.read(CHUNK), b""):
                digest.update(chunk)
        return digest.hexdigest() == entry["sha256"]
    except OSError:
        return False


def _locked(exc: OSError) -> bool:
    """A file held open by another program (Windows sharing violation, EACCES/EPERM)."""
    return isinstance(exc, PermissionError)


def _sync_uploads(zf: zipfile.ZipFile, files: dict) -> list[str]:
    """Make uploads/media and uploads/thumbs match the archive. Files that cannot be
    replaced or deleted because they are locked (Windows) are reported, not raised. Any
    other write error (disk full, I/O error) raises, so restore() puts the safety backup
    back. Extra files are deleted only after every archive file was written."""
    root = Path(current_app.config["UPLOAD_FOLDER"])
    wanted: dict[str, dict[str, tuple[str, dict]]] = {sub: {} for sub in UPLOAD_SUBDIRS}
    for member, entry in files.items():
        match = UPLOAD_MEMBER_RE.match(member)
        if match:
            wanted[match.group(1)][match.group(2)] = (member, entry)
    skipped = []
    for sub, entries in wanted.items():
        folder = root / sub
        folder.mkdir(parents=True, exist_ok=True)
        for fname, (member, entry) in entries.items():
            dest = _inside(folder, fname)
            if _same_file(dest, entry):
                continue
            tmp = folder / f".restore-{secrets.token_hex(4)}-{fname}"
            try:
                with zf.open(member) as src, open(tmp, "wb") as out:
                    shutil.copyfileobj(src, out, CHUNK)
                os.replace(tmp, dest)
            except OSError as exc:
                service._remove(tmp)
                if not _locked(exc):
                    raise RestoreError(f"Could not write {sub}/{fname}: {exc}") from exc
                skipped.append(f"{sub}/{fname}: {exc}")
    if skipped:
        log.warning("Restore kept the extra upload files: %d archive files were not "
                    "written", len(skipped))
        return skipped
    for sub, entries in wanted.items():
        folder = root / sub
        for path in folder.iterdir():
            if path.name.startswith(".") or path.name in entries or not path.is_file():
                continue
            try:
                path.unlink()
            except OSError as exc:
                skipped.append(f"{sub}/{path.name}: {exc}")
    return skipped


def _write_secret(data: bytes) -> None:
    """Restore instance/secret_key unless the key comes from AISIGNX_SECRET_KEY.
    It takes effect at the next restart."""
    if os.environ.get("AISIGNX_SECRET_KEY"):
        return
    folder = Path(current_app.instance_path)
    tmp = folder / f".secret_key-{secrets.token_hex(4)}"
    tmp.write_bytes(data)
    os.replace(tmp, folder / "secret_key")


def _upgrade() -> None:
    """Bring an older restored database up to this version's schema."""
    if dbinfo.current_revision() == dbinfo.head_revision():
        return
    from flask_migrate import upgrade

    upgrade()


def _notify_screens() -> None:
    try:
        from ..player.broker import get_broker

        broker = get_broker()
        if broker is not None:
            broker.publish_all("manifest", {})
    except Exception:
        log.exception("Could not notify screens after the restore")
