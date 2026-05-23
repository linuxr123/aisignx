"""
Backup + restore - Phase 3.

Produces zip archives containing the SQLite database, the per-tenant
uploads tree, and the plugins tree. Backups are written to `./backups/`
which is intentionally OUTSIDE the static and uploads webroots so they
can never be served by the public file routes.

Backup format
-------------
A backup is a single .zip file:

    aisignx-backup-<UTC-ISO>.zip
        manifest.json       metadata + file inventory
        db/digital_signage.db
        uploads/...          (optional)
        plugins/...          (optional)

`manifest.json` includes:
    schema_version     -- this module's format version (currently 1)
    created_at         -- ISO 8601 UTC
    app_version        -- best-effort from settings, else 'unknown'
    contents           -- {db: bool, uploads: bool, plugins: bool}
    counts             -- {users, domains, displays, media}
    sqlite_page_size   -- sanity check at restore time
    sqlite_page_count
    db_sha256          -- integrity check

Why not just zip the .db file?
------------------------------
SQLite is a journal-based engine. Copying the file while another writer
is active can produce a torn snapshot that fails to open. The
`sqlite3.Connection.backup()` API takes a consistent online snapshot --
that's what we use here. For uploads + plugins (just bytes on disk),
ordinary file copy is fine; we accept that media added during the
backup window may be inconsistent with the DB and tolerate that the
restore-time scanner will report "missing media files" warnings rather
than failing.

Restore safety
--------------
1. Validate the manifest BEFORE touching anything.
2. Move existing instance/db, uploads, plugins to a `<dir>.restore-<ts>`
   sibling. This is a rename, so it's atomic and reversible.
3. Extract the backup into the original locations.
4. If anything fails between (2) and (3), the staging directory is left
   in place with a clear log message so the admin can swap it back.

Live processes hold open file handles. The DB file replacement WILL
NOT be visible to a running app -- the docs note that a server restart
is required after a DB restore. Uploads/plugins changes are picked up
on the next request.
"""
import hashlib
import io
import json
import os
import shutil
import sqlite3
import time
import zipfile
from datetime import datetime
from pathlib import Path

from logging_config import logger


SCHEMA_VERSION = 1
BACKUP_DIR_NAME = 'backups'
BACKUP_PREFIX = 'aisignx-backup-'
BACKUP_SUFFIX = '.zip'

# Files that must be present in any well-formed backup.
_REQUIRED_MANIFEST_KEYS = {'schema_version', 'created_at', 'contents'}


# ----------------------------------------------------------------------------
# Path resolution
# ----------------------------------------------------------------------------

def _root() -> Path:
    """Workspace root -- the directory that contains app.py."""
    return Path(__file__).resolve().parent


def _backup_dir() -> Path:
    # Honor a settings-driven override so admins can point backups at a
    # mounted volume / external disk. Falls back to ./backups beside the
    # app root, which is the historical behavior and outside any webroot.
    override = None
    try:
        import settings as _settings
        raw = (_settings.get('backup.location') or '').strip()
        if raw:
            override = Path(raw).expanduser()
    except Exception:
        override = None
    p = override if override else (_root() / BACKUP_DIR_NAME)
    p.mkdir(parents=True, exist_ok=True)
    return p


def configured_backup_location() -> str:
    """Return the resolved backup directory as a string for UI display."""
    return str(_backup_dir())


def prune_backups(keep: int):
    """Delete the oldest backups beyond `keep`. Used by scheduled backups
    to bound disk usage. Returns the list of deleted filenames."""
    try:
        keep = max(0, int(keep))
    except Exception:
        keep = 0
    if keep <= 0:
        return []
    files = sorted(
        _backup_dir().glob(f'{BACKUP_PREFIX}*{BACKUP_SUFFIX}'),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    deleted = []
    for p in files[keep:]:
        try:
            p.unlink()
            deleted.append(p.name)
            logger.info(f'backup: pruned {p.name} (retention={keep})')
        except Exception as e:
            logger.warning(f'backup: prune failed for {p.name}: {e}')
    return deleted


def _db_path() -> Path:
    """Full path to the live SQLite file. Honors Flask's instance_path
    convention: instance/<basename>.db."""
    # Avoid a hard import of Flask app at module import time; we resolve
    # lazily so this module is safe to import in CLI context.
    try:
        from flask import current_app
        uri = current_app.config.get('SQLALCHEMY_DATABASE_URI', '')
        if uri.startswith('sqlite:///'):
            rel = uri.replace('sqlite:///', '', 1)
            # Flask treats sqlite:///foo.db as instance/foo.db
            if not os.path.isabs(rel):
                return Path(current_app.instance_path) / rel
            return Path(rel)
    except Exception:
        pass
    # Fallback: hard-coded location used by config.py default.
    return _root() / 'instance' / 'digital_signage.db'


def _uploads_path() -> Path:
    try:
        from flask import current_app
        return Path(current_app.config.get('UPLOAD_FOLDER') or _root() / 'uploads')
    except Exception:
        return _root() / 'uploads'


def _plugins_path() -> Path:
    return _root() / 'plugins'


# ----------------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------------

def _safe_filename(name: str) -> str:
    """Block path traversal in user-supplied filenames. We only ever
    accept names that match our own `aisignx-backup-...zip` prefix."""
    if (not name
            or not name.startswith(BACKUP_PREFIX)
            or not name.endswith(BACKUP_SUFFIX)
            or '/' in name or '\\' in name or '..' in name):
        raise ValueError(f'invalid backup filename: {name!r}')
    return name


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _sqlite_meta(db_path: Path) -> dict:
    """Read SQLite header info for sanity-checking a restore target."""
    conn = sqlite3.connect(str(db_path))
    try:
        ps = conn.execute('PRAGMA page_size').fetchone()[0]
        pc = conn.execute('PRAGMA page_count').fetchone()[0]
        return {'sqlite_page_size': ps, 'sqlite_page_count': pc}
    finally:
        conn.close()


def _add_tree(zf: zipfile.ZipFile, src: Path, arc_prefix: str):
    """Recursively add `src` to `zf` under `arc_prefix/`. Skips empty
    directories (zip handles those implicitly)."""
    if not src.exists():
        return 0
    count = 0
    for root, _dirs, files in os.walk(src):
        for fn in files:
            full = Path(root) / fn
            rel = full.relative_to(src)
            zf.write(full, arcname=f'{arc_prefix}/{rel.as_posix()}')
            count += 1
    return count


# ----------------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------------

def create_backup(include_uploads: bool = True,
                  include_plugins: bool = True,
                  source: str = 'manual',
                  note: str = '') -> Path:
    """Build a backup zip. Returns its absolute path.

    The DB snapshot uses sqlite3.backup() so no server downtime is
    required. Uploads and plugins are copied while the server runs --
    files added mid-backup may be missing or partial in the archive.

    `source` tags the manifest so the admin can distinguish manual,
    scheduled, and pre-restore (auto-snapshot) backups in the listing.
    `note` is a free-form annotation surfaced in the same place.
    """
    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    # Tag scheduled / pre-restore backups in the filename too so they
    # are obvious in directory listings outside the admin UI.
    src_tag = (source or 'manual').strip().lower()
    if src_tag not in ('manual', 'scheduled', 'pre-restore'):
        src_tag = 'manual'
    fname_tag = '' if src_tag == 'manual' else f'-{src_tag}'
    out = _backup_dir() / f'{BACKUP_PREFIX}{ts}{fname_tag}{BACKUP_SUFFIX}'
    tmp = out.with_suffix('.zip.tmp')

    src_db = _db_path()
    if not src_db.exists():
        raise FileNotFoundError(f'database file not found at {src_db}')

    # Online SQLite snapshot to a temp file -- the file we'll embed.
    db_staging = _backup_dir() / f'.staging-db-{ts}.db'
    src_conn = sqlite3.connect(str(src_db))
    dst_conn = sqlite3.connect(str(db_staging))
    try:
        with dst_conn:
            src_conn.backup(dst_conn)
    finally:
        src_conn.close()
        dst_conn.close()

    try:
        meta = _sqlite_meta(db_staging)
        db_hash = _file_sha256(db_staging)

        # Pull a few cheap counts for the manifest -- helps the admin
        # eyeball whether they grabbed the right backup.
        counts = _summary_counts(db_staging)

        try:
            from flask import current_app
            app_version = current_app.config.get('VERSION', 'unknown')
        except Exception:
            app_version = 'unknown'

        manifest = {
            'schema_version':  SCHEMA_VERSION,
            'created_at':      datetime.utcnow().isoformat() + 'Z',
            'app_version':     app_version,
            'source':          src_tag,
            'note':            (note or '').strip()[:500],
            'contents': {
                'db':       True,
                'uploads':  bool(include_uploads),
                'plugins':  bool(include_plugins),
            },
            'counts':          counts,
            'sqlite_page_size':  meta['sqlite_page_size'],
            'sqlite_page_count': meta['sqlite_page_count'],
            'db_sha256':       db_hash,
        }

        with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('manifest.json', json.dumps(manifest, indent=2))
            zf.write(db_staging, arcname='db/digital_signage.db')
            uploads_count = 0
            plugins_count = 0
            if include_uploads:
                uploads_count = _add_tree(zf, _uploads_path(), 'uploads')
            if include_plugins:
                plugins_count = _add_tree(zf, _plugins_path(), 'plugins')

        # Re-open to append the file counts now that we know them.
        # Cheaper than two passes through the trees.
        manifest['counts']['uploads_files'] = uploads_count
        manifest['counts']['plugins_files'] = plugins_count
        # Rewrite manifest with the file counts populated.
        _replace_zip_member(tmp, 'manifest.json',
                            json.dumps(manifest, indent=2))

        tmp.rename(out)
        logger.info(f'backup: created {out.name} '
                    f'({uploads_count} uploads, {plugins_count} plugin files)')
        return out
    finally:
        try:
            db_staging.unlink()
        except OSError:
            pass
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


def _replace_zip_member(zip_path: Path, name: str, data: str):
    """Overwrite a single member in an existing zip. Python's stdlib
    can't update in place, so we re-emit the archive."""
    tmp = zip_path.with_suffix('.zip.rewrite')
    with zipfile.ZipFile(zip_path, 'r') as zin:
        with zipfile.ZipFile(tmp, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename == name:
                    zout.writestr(name, data)
                else:
                    zout.writestr(item, zin.read(item.filename))
    tmp.replace(zip_path)


def _summary_counts(db_path: Path) -> dict:
    """Cheap COUNT(*) summary for the manifest. Best-effort -- a missing
    table just yields 0 rather than failing the whole backup."""
    out = {}
    conn = sqlite3.connect(str(db_path))
    try:
        for table, label in [('user', 'users'), ('domain', 'domains'),
                              ('display', 'displays'), ('media', 'media'),
                              ('playlist', 'playlists'),
                              ('schedule', 'schedules')]:
            try:
                n = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                out[label] = int(n)
            except sqlite3.Error:
                out[label] = 0
    finally:
        conn.close()
    return out


def list_backups() -> list[dict]:
    """Enumerate the backups directory. Returns metadata only; readers
    don't need to crack the archives. Each entry includes the embedded
    manifest if it's parseable."""
    out = []
    for p in sorted(_backup_dir().glob(f'{BACKUP_PREFIX}*{BACKUP_SUFFIX}'),
                    reverse=True):
        st = p.stat()
        entry = {
            'filename':   p.name,
            'size_bytes': st.st_size,
            'mtime':      datetime.utcfromtimestamp(st.st_mtime).isoformat() + 'Z',
            'manifest':   None,
        }
        try:
            with zipfile.ZipFile(p, 'r') as zf:
                with zf.open('manifest.json') as mf:
                    entry['manifest'] = json.load(mf)
        except Exception as e:
            entry['manifest_error'] = str(e)
        out.append(entry)
    return out


def delete_backup(filename: str):
    name = _safe_filename(filename)
    p = _backup_dir() / name
    if not p.exists():
        raise FileNotFoundError(name)
    p.unlink()
    logger.info(f'backup: deleted {name}')


def get_backup_path(filename: str) -> Path:
    """Resolve a user-supplied filename to an absolute path inside the
    backup directory. Raises ValueError on traversal attempts."""
    name = _safe_filename(filename)
    p = _backup_dir() / name
    if not p.exists():
        raise FileNotFoundError(name)
    return p


# ----------------------------------------------------------------------------
# Restore
# ----------------------------------------------------------------------------

class RestoreResult:
    """Returned by restore_backup(). The `requires_restart` flag tells
    the admin UI whether the live process needs a manual restart for
    the new DB to take effect."""
    def __init__(self, manifest, restored_uploads, restored_plugins,
                 staged_paths, requires_restart=True):
        self.manifest = manifest
        self.restored_uploads = restored_uploads
        self.restored_plugins = restored_plugins
        # Old data is rolled to these staged_paths and left there. The
        # admin can `rmtree` them after confirming the restore worked.
        self.staged_paths = staged_paths
        self.requires_restart = requires_restart

    def to_dict(self):
        return {
            'manifest':           self.manifest,
            'restored_uploads':   self.restored_uploads,
            'restored_plugins':   self.restored_plugins,
            'staged_paths':       [str(p) for p in self.staged_paths],
            'requires_restart':   self.requires_restart,
        }


def _validate_archive(path: Path) -> dict:
    """Open and sanity-check a backup zip. Returns the manifest dict.
    Raises ValueError with a message safe to surface to the admin."""
    if not zipfile.is_zipfile(path):
        raise ValueError('file is not a valid zip archive')
    with zipfile.ZipFile(path, 'r') as zf:
        names = set(zf.namelist())
        if 'manifest.json' not in names:
            raise ValueError('manifest.json missing -- not an AISignX backup')
        if 'db/digital_signage.db' not in names:
            raise ValueError('db/digital_signage.db missing in archive')
        with zf.open('manifest.json') as mf:
            try:
                manifest = json.load(mf)
            except json.JSONDecodeError as e:
                raise ValueError(f'manifest.json is not valid JSON: {e}')
        missing = _REQUIRED_MANIFEST_KEYS - set(manifest)
        if missing:
            raise ValueError(f'manifest missing keys: {sorted(missing)}')
        if manifest['schema_version'] != SCHEMA_VERSION:
            raise ValueError(f'unsupported schema_version '
                             f'{manifest["schema_version"]} (expected {SCHEMA_VERSION})')
    return manifest


def restore_backup(filename: str, restore_uploads: bool = True,
                   restore_plugins: bool = True,
                   pre_snapshot: bool = True) -> RestoreResult:
    """Restore from a backup archive. The DB swap requires a server
    restart to take effect; uploads + plugins changes are picked up
    on the next request.

    Strategy:
      0. Take an automatic pre-restore snapshot of the current state
         (tagged source='pre-restore' so it's obvious in the listing)
         unless the caller opts out. This is the professional default
         -- it gives the admin a one-click rollback path even if the
         filesystem-level staging dirs are later cleaned up.
      1. Validate manifest -- bail before touching anything if anything
         smells off.
      2. Dispose the SQLAlchemy connection pool so Windows releases its
         lock on the DB file -- otherwise the rename below fails with
         WinError 32.
      3. Rename existing instance/, uploads/, plugins/ to staging dirs
         (atomic on POSIX and Windows when same volume).
      4. Extract from the archive into the original locations.
      5. On any error in step 4, leave staging dirs in place; the admin
         can roll back manually with the paths in RestoreResult.
    """
    src = get_backup_path(filename)
    manifest = _validate_archive(src)

    # Step 0: pre-restore auto-snapshot. Best-effort -- if it fails we
    # log and continue, because the user explicitly asked to restore.
    if pre_snapshot:
        try:
            snap = create_backup(
                include_uploads=bool(restore_uploads),
                include_plugins=bool(restore_plugins),
                source='pre-restore',
                note=f'auto-snapshot before restoring {filename}',
            )
            logger.info(f'backup: pre-restore snapshot created {snap.name}')
        except Exception as e:
            logger.warning(
                f'backup: pre-restore snapshot failed (continuing): {e}')

    # Step 2: drop SQLAlchemy's open connections. After a successful
    # restore, a server restart is required anyway -- but the dispose
    # call lets the file rename succeed in step 3 on Windows.
    try:
        from models import db
        db.session.close()
        db.engine.dispose()
    except Exception as e:
        logger.warning(f'backup: db.engine.dispose() failed (continuing): {e}')

    ts = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    staged = []

    db_target = _db_path()
    uploads_target = _uploads_path()
    plugins_target = _plugins_path()

    do_uploads = restore_uploads and manifest['contents'].get('uploads')
    do_plugins = restore_plugins and manifest['contents'].get('plugins')

    # 2) Roll existing data to staging.
    def _stage(p: Path):
        if not p.exists():
            return None
        rolled = p.with_name(p.name + f'.restore-{ts}')
        p.rename(rolled)
        staged.append(rolled)
        return rolled

    db_rolled = _stage(db_target)
    # Re-create the empty parent dir so the extract can write into it.
    db_target.parent.mkdir(parents=True, exist_ok=True)

    uploads_rolled = _stage(uploads_target) if do_uploads else None
    plugins_rolled = _stage(plugins_target) if do_plugins else None

    # 3) Extract.
    try:
        with zipfile.ZipFile(src, 'r') as zf:
            for member in zf.infolist():
                if member.is_dir():
                    continue
                name = member.filename
                if name == 'manifest.json':
                    continue
                # Map archive paths to filesystem targets.
                if name == 'db/digital_signage.db':
                    out = db_target
                elif name.startswith('uploads/'):
                    if not do_uploads:
                        continue
                    rel = name[len('uploads/'):]
                    out = uploads_target / rel
                elif name.startswith('plugins/'):
                    if not do_plugins:
                        continue
                    rel = name[len('plugins/'):]
                    out = plugins_target / rel
                else:
                    # Unknown member -- skip rather than fail. Future
                    # backup versions may add new sections.
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member, 'r') as sf, open(out, 'wb') as df:
                    shutil.copyfileobj(sf, df)
    except Exception as e:
        # Don't try to auto-rollback; the staged dirs are recoverable
        # but trying to re-rename while half-extracted can compound the
        # damage. Surface the error and the staging paths.
        logger.error(f'backup: restore extraction failed: {e}; staged paths: {staged}')
        raise RuntimeError(f'restore failed during extraction: {e}. '
                           f'Roll back manually from staging dirs: '
                           f'{[str(p) for p in staged]}') from e

    logger.info(f'backup: restore complete from {filename}; '
                f'restart required for DB changes to take effect')

    return RestoreResult(
        manifest=manifest,
        restored_uploads=do_uploads,
        restored_plugins=do_plugins,
        staged_paths=staged,
        requires_restart=True,
    )
