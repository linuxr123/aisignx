"""
Resolve and apply the tenant media storage root (uploads tree).

Superadmins set ``disk.upload_root`` in System Settings (global scope).
When empty, the server uses ``UPLOAD_FOLDER`` from config.py / environment.

Layout under the root::

    <upload_root>/
        d1/images/...
        d2/videos/...
"""
from __future__ import annotations

import os
import shutil
import string
import sys
from pathlib import Path

from logging_config import logger

_SERVER_ROOT = Path(__file__).resolve().parent
_CONFIG_DEFAULT: Path | None = None


def server_root() -> Path:
    return _SERVER_ROOT


def config_default_upload_root(app) -> Path:
    """Filesystem path from config.py before any settings override."""
    raw = app.config.get('UPLOAD_FOLDER', 'uploads')
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _SERVER_ROOT / p
    return p.resolve()


def _resolve_path_string(path_str: str, app) -> Path:
    """Resolve a path string to an absolute directory path."""
    raw = (path_str or '').strip()
    if not raw:
        if _CONFIG_DEFAULT is None:
            _CONFIG_DEFAULT = config_default_upload_root(app)
        return _CONFIG_DEFAULT
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (_SERVER_ROOT / p).resolve()
    else:
        p = p.resolve()
    return p


def resolve_path_from_setting_value(path_str: str | None, app) -> Path:
    """Target upload root for a proposed ``disk.upload_root`` setting value."""
    return _resolve_path_string(path_str or '', app)


def validate_upload_root_value(path_str: str | None) -> tuple[bool, str | None]:
    """
    Validate a proposed ``disk.upload_root`` value.
    Returns (ok, error_message). Empty/whitespace means "use config default".
    """
    raw = (path_str or '').strip()
    if not raw:
        return True, None
    if '\0' in raw:
        return False, 'Path cannot contain null characters.'
    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_SERVER_ROOT / p).resolve()
        else:
            p = p.resolve()
    except (OSError, ValueError) as exc:
        return False, f'Invalid path: {exc}'
    if p.is_file():
        return False, 'Path must be a directory, not a file.'
    try:
        p.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f'Cannot create directory: {exc}'
    if not os.access(p, os.W_OK):
        return False, 'Directory is not writable by the server process.'
    return True, None


def _paths_nested(a: Path, b: Path) -> bool:
    """True if one path is inside the other."""
    a = a.resolve()
    b = b.resolve()
    if a == b:
        return True
    try:
        a.relative_to(b)
        return True
    except ValueError:
        pass
    try:
        b.relative_to(a)
        return True
    except ValueError:
        return False


def _remove_empty_dirs(root: Path) -> None:
    """Remove empty directories under root (bottom-up)."""
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(str(root), topdown=False):
        if dirpath == str(root):
            continue
        p = Path(dirpath)
        try:
            if not any(p.iterdir()):
                p.rmdir()
        except OSError:
            pass


def migrate_upload_tree(
    source: Path,
    dest: Path,
    *,
    move: bool = True,
    dry_run: bool = False,
) -> dict:
    """
    Copy or move all files from source upload root into dest.
    Skips files that already exist at the destination path.
    """
    source = source.resolve()
    dest = dest.resolve()
    result = {
        'source': str(source),
        'destination': str(dest),
        'dry_run': dry_run,
        'move': move,
        'skipped_same_path': False,
        'files_transferred': 0,
        'files_skipped': 0,
        'bytes_transferred': 0,
        'errors': [],
    }

    if source == dest:
        result['skipped_same_path'] = True
        result['message'] = 'Source and destination are the same path.'
        return result

    if _paths_nested(source, dest):
        result['errors'].append('Source and destination paths cannot contain each other.')
        return result

    if not source.is_dir():
        result['message'] = 'Source directory does not exist; nothing to move.'
        return result

    ok, err = validate_upload_root_value(str(dest))
    if not ok:
        result['errors'].append(err or 'Invalid destination path.')
        return result

    if not dry_run:
        dest.mkdir(parents=True, exist_ok=True)

    for root, _dirs, files in os.walk(source, topdown=True):
        root_path = Path(root)
        try:
            rel = root_path.relative_to(source)
        except ValueError:
            continue
        dest_dir = dest / rel
        for fname in files:
            src_file = root_path / fname
            if not src_file.is_file():
                continue
            dest_file = dest_dir / fname
            if dest_file.exists():
                result['files_skipped'] += 1
                continue
            try:
                size = src_file.stat().st_size
            except OSError as exc:
                result['errors'].append(f'{src_file}: {exc}')
                continue
            if dry_run:
                result['files_transferred'] += 1
                result['bytes_transferred'] += size
                continue
            try:
                dest_dir.mkdir(parents=True, exist_ok=True)
                if move:
                    shutil.move(str(src_file), str(dest_file))
                else:
                    shutil.copy2(str(src_file), str(dest_file))
                result['files_transferred'] += 1
                result['bytes_transferred'] += size
            except OSError as exc:
                result['errors'].append(f'{src_file} -> {dest_file}: {exc}')

    if move and not dry_run and not result['errors']:
        _remove_empty_dirs(source)

    if result['errors']:
        result['message'] = (
            f"Migration finished with {len(result['errors'])} error(s). "
            f"Transferred {result['files_transferred']} file(s), "
            f"skipped {result['files_skipped']} existing file(s)."
        )
    elif result['files_transferred'] or result['files_skipped']:
        action = 'Would transfer' if dry_run else ('Moved' if move else 'Copied')
        result['message'] = (
            f"{action} {result['files_transferred']} file(s) "
            f"({result['bytes_transferred']} bytes). "
            f"Skipped {result['files_skipped']} file(s) already at destination."
        )
    else:
        result['message'] = 'No files found to transfer.'

    logger.info(
        'upload_storage migrate %s -> %s: transferred=%s skipped=%s dry_run=%s',
        source, dest, result['files_transferred'], result['files_skipped'], dry_run,
    )
    return result


def resolve_upload_root(app=None) -> Path:
    """Active uploads root: settings override or config default."""
    global _CONFIG_DEFAULT
    if app is not None:
        if _CONFIG_DEFAULT is None:
            _CONFIG_DEFAULT = config_default_upload_root(app)
        base_default = _CONFIG_DEFAULT
    else:
        from flask import current_app
        app = current_app
        if _CONFIG_DEFAULT is None:
            _CONFIG_DEFAULT = config_default_upload_root(app)
        base_default = _CONFIG_DEFAULT

    import settings as settings_module
    override = (settings_module.get('disk.upload_root') or '').strip()
    if not override:
        return base_default

    ok, err = validate_upload_root_value(override)
    if not ok:
        logger.warning('disk.upload_root invalid (%s); using config default: %s',
                       err, base_default)
        return base_default

    p = Path(override).expanduser()
    if not p.is_absolute():
        p = (_SERVER_ROOT / p).resolve()
    else:
        p = p.resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def apply_from_settings(app) -> tuple[bool, str | None]:
    """Sync Flask ``UPLOAD_FOLDER`` from settings. Returns (ok, error)."""
    import settings as settings_module
    override = (settings_module.get('disk.upload_root') or '').strip()
    if override:
        ok, err = validate_upload_root_value(override)
        if not ok:
            return False, err
    root = resolve_upload_root(app)
    app.config['UPLOAD_FOLDER'] = str(root)
    logger.info('Tenant upload storage root: %s', root)
    return True, None


def _path_browser_parent(p: Path) -> str | None:
    """Parent directory for the path browser; empty string means drive list (Windows)."""
    if sys.platform == 'win32':
        s = str(p)
        if len(s) == 3 and s[1] == ':' and s[2] in ('\\', '/'):
            return ''
    try:
        parent = p.parent
    except (ValueError, OSError):
        return None
    if parent == p:
        return None
    return str(parent.resolve())


def list_server_directories(path_str: str | None = None, app=None) -> dict:
    """
    List directories on the server for superadmin path selection.
    Empty path_str returns OS roots (drives on Windows, / on Unix).
    """
    if app is None:
        from flask import current_app
        app = current_app

    shortcuts = [
        {'label': 'Server upload root', 'path': str(resolve_upload_root(app))},
        {'label': 'Application directory', 'path': str(_SERVER_ROOT)},
    ]

    raw = (path_str or '').strip()
    if not raw:
        entries = []
        if sys.platform == 'win32':
            for letter in string.ascii_uppercase:
                # NOTE: Path('C:') means "current dir on drive C:", which
                # resolves to the CWD -- not the drive root. Always include
                # the trailing separator so we get 'C:\' (the actual root).
                root = f'{letter}:\\'
                try:
                    if os.path.exists(root):
                        entries.append({
                            'name': f'{letter}:',
                            'path': root,
                        })
                except OSError:
                    pass
        else:
            entries.append({'name': '/', 'path': '/'})
        return {
            'path': None,
            'parent': None,
            'entries': entries,
            'shortcuts': shortcuts,
            'writable': None,
        }

    try:
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = (_SERVER_ROOT / p).resolve()
        else:
            p = p.resolve()
    except (OSError, ValueError) as exc:
        return {'errors': [f'Invalid path: {exc}']}

    if not p.exists():
        return {'errors': ['Path does not exist.']}
    if not p.is_file() and not p.is_dir():
        return {'errors': ['Path is not accessible.']}
    if p.is_file():
        p = p.parent

    parent = _path_browser_parent(p)
    entries = []
    try:
        names = sorted(os.listdir(p), key=lambda x: x.lower())
    except PermissionError:
        return {'errors': ['Permission denied reading this folder.']}
    except OSError as exc:
        return {'errors': [str(exc)]}

    for name in names:
        if name.startswith('.'):
            continue
        full = p / name
        try:
            if full.is_dir():
                entries.append({
                    'name': name,
                    'path': str(full.resolve()),
                })
        except OSError:
            continue

    return {
        'path': str(p),
        'parent': parent,
        'entries': entries,
        'shortcuts': shortcuts,
        'writable': os.access(p, os.W_OK),
    }


def configured_upload_root_display() -> dict:
    """Summary for admin APIs."""
    import settings as settings_module
    from flask import current_app
    override = (settings_module.get('disk.upload_root') or '').strip()
    resolved = resolve_upload_root()
    default = _CONFIG_DEFAULT or config_default_upload_root(current_app)
    return {
        'setting': override,
        'resolved_path': str(resolved),
        'config_default_path': str(default),
        'uses_override': bool(override),
    }


def default_tenant_dir(domain_id: int, app=None) -> Path:
    """Standard folder for a tenant under the server upload root."""
    root = resolve_upload_root(app)
    return root / f'd{int(domain_id)}'


def resolve_tenant_root(domain_id: int, app=None) -> Path:
    """Effective filesystem root for one tenant's media."""
    from flask import current_app
    from models import Domain, db as _db
    from tenant_filter import bypass_tenant_filter

    if app is None:
        app = current_app

    with bypass_tenant_filter():
        d = _db.session.get(Domain, int(domain_id))

    custom = (d.storage_root_path or '').strip() if d else ''
    if custom:
        ok, err = validate_upload_root_value(custom)
        if ok:
            p = Path(custom).expanduser()
            if not p.is_absolute():
                p = (_SERVER_ROOT / p).resolve()
            else:
                p = p.resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p
    return default_tenant_dir(domain_id, app)


def tenant_storage_info(domain_id: int, app=None) -> dict:
    """Paths for superadmin tenant storage UI."""
    from models import Domain
    from models import db as _db
    from tenant_filter import bypass_tenant_filter

    with bypass_tenant_filter():
        d = _db.session.get(Domain, int(domain_id))
    default_path = default_tenant_dir(domain_id, app)
    resolved = resolve_tenant_root(domain_id, app)
    custom = (d.storage_root_path or '').strip() if d else ''
    return {
        'domain_id': int(domain_id),
        'tenant_name': d.name if d else None,
        'storage_root_path': custom or None,
        'default_path': str(default_path),
        'resolved_path': str(resolved),
        'uses_custom_path': bool(custom),
        'server_upload_root': str(resolve_upload_root(app)),
    }


def migrate_tenant_storage(
    domain_id: int,
    new_root_path: str | None,
    *,
    move: bool = True,
    dry_run: bool = False,
    app=None,
) -> dict:
    """
    Move one tenant's files to a new root directory and optionally persist
    ``Domain.storage_root_path``. Pass empty new_root_path to revert to default
    folder (does not move files back).
    """
    from models import Domain
    from models import db as _db
    from tenant_filter import bypass_tenant_filter

    domain_id = int(domain_id)
    with bypass_tenant_filter():
        d = _db.session.get(Domain, domain_id)
    if d is None:
        return {'errors': ['Tenant not found.']}

    old_root = resolve_tenant_root(domain_id, app)
    raw = (new_root_path or '').strip()

    result = {
        'domain_id': domain_id,
        'old_path': str(old_root),
        'new_path': None,
        'migration': None,
        'cleared_custom_path': False,
    }

    if not raw:
        if not dry_run:
            d.storage_root_path = None
            _db.session.commit()
        result['cleared_custom_path'] = True
        result['new_path'] = str(default_tenant_dir(domain_id, app))
        result['message'] = (
            'Reverted to default storage folder. Files were not moved; '
            'ensure media files exist under the default path if you previously '
            'used a custom location.'
        )
        return result

    ok, err = validate_upload_root_value(raw)
    if not ok:
        return {'errors': [err or 'Invalid path.']}

    new_root = Path(raw).expanduser()
    if not new_root.is_absolute():
        new_root = (_SERVER_ROOT / new_root).resolve()
    else:
        new_root = new_root.resolve()
    result['new_path'] = str(new_root)

    migration = None
    if old_root != new_root:
        migration = migrate_upload_tree(old_root, new_root, move=move, dry_run=dry_run)
        result['migration'] = migration
        if migration.get('errors'):
            result['errors'] = migration['errors']
            return result

    if not dry_run:
        d.storage_root_path = str(new_root)
        _db.session.commit()
        result['message'] = (
            (migration or {}).get('message')
            or f'Tenant storage path set to {new_root}'
        )
    else:
        result['message'] = 'Dry run complete.'
    return result
