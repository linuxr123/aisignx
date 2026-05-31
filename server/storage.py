"""
Tenant-scoped media storage - Phase 1, Task 6.

A small abstraction over the filesystem so route handlers don't have to
think about per-tenant paths, quota enforcement, sandboxing or accounting.

Layout
------
    uploads/
        d1/                       <- domain_id 1
            images/<uuid>.jpg
            videos/<uuid>.mp4
            thumbnails/<filename>_thumb.png
            misc/                 <- catch-all for variants/derivatives
        d2/
            ...

A `StoredFile` is a small dataclass returned by save_*() with everything
the caller needs to populate a Media row:

    rel_path   uploads-relative POSIX path:  'd1/images/abc.jpg'
    abs_path   absolute filesystem path
    size       file size in bytes
    mime       guessed mime type or None

Public API
----------
    save_upload(file_storage, kind)               -> StoredFile
    save_bytes(data, kind, ext)                   -> StoredFile
    save_thumbnail(source_abs, name_hint, kind)   -> StoredFile | None
    delete(rel_path)                              -> bool
    absolute_path(rel_path)                       -> str | None
    serve_url(rel_path)                           -> str
    check_quota(size_bytes)                       -> (ok, message)
    recompute_used(domain_id)                     -> int
    install_storage_accounting(db)                -> None  (call once at startup)

All write APIs use current_domain_id() implicitly. Callers who need to
write into a different tenant (admin tooling) wrap in
bypass_tenant_filter() AND pass an explicit domain_id= argument.

Quota
-----
Domain.storage_used_bytes is updated automatically via SQLAlchemy events
on Media insert/delete. Quota check rejects uploads that would exceed
Domain.storage_quota_bytes (None = unlimited).
"""
import os
import uuid
import mimetypes
from dataclasses import dataclass

from flask import current_app, url_for
from sqlalchemy import event, func
from sqlalchemy.orm import Session
from werkzeug.utils import secure_filename

from logging_config import logger
from models import db, Domain, Media
from tenant_filter import current_domain_id, bypass_tenant_filter


# Allowed extension whitelists per kind. Keep tight; route handlers can
# pre-validate before calling save_upload, but defense in depth.
ALLOWED_EXTENSIONS = {
    'image':     {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tif', '.tiff', '.svg'},
    'video':     {'.mp4', '.webm', '.ogg', '.mov'},
    'thumbnail': {'.png', '.jpg', '.jpeg', '.webp'},
    'misc':      None,    # any
}

# Map kind -> subfolder name on disk.
_KIND_FOLDER = {
    'image':     'images',
    'video':     'videos',
    'thumbnail': 'thumbnails',
    'misc':      'misc',
}


@dataclass(frozen=True)
class StoredFile:
    rel_path: str       # 'd1/images/<uuid>.jpg'  (always forward slashes)
    abs_path: str
    size:     int
    mime:     str | None


# -----------------------------------------------------------------------------
# Path helpers
# -----------------------------------------------------------------------------
def _uploads_root() -> str:
    import upload_paths
    return str(upload_paths.resolve_upload_root())


def _tenant_root(domain_id: int) -> str:
    """Absolute path of a tenant's storage directory. Created on demand."""
    if domain_id is None:
        raise RuntimeError('storage operation requires a tenant context')
    import upload_paths
    return str(upload_paths.resolve_tenant_root(int(domain_id)))


def _resolve(rel_path: str) -> str | None:
    """Resolve an uploads-relative path to an absolute one, sandboxed.
    Returns None if the path escapes the allowed tenant/upload roots."""
    if not rel_path:
        return None
    norm = rel_path.replace('\\', '/').lstrip('/')
    uploads = _uploads_root()
    candidate = os.path.abspath(os.path.join(uploads, norm))
    if candidate == uploads or candidate.startswith(uploads + os.sep):
        return candidate

    parts = norm.split('/', 1)
    if parts[0].startswith('d') and parts[0][1:].isdigit():
        try:
            did = int(parts[0][1:])
            suffix = parts[1] if len(parts) > 1 else ''
            tenant_root = _tenant_root(did)
            candidate = (
                os.path.abspath(os.path.join(tenant_root, suffix))
                if suffix else os.path.abspath(tenant_root)
            )
            tr = os.path.abspath(tenant_root)
            if candidate == tr or candidate.startswith(tr + os.sep):
                return candidate
        except (ValueError, TypeError):
            pass
    return None


def _ensure_kind(kind: str) -> str:
    if kind not in _KIND_FOLDER:
        raise ValueError(f'unknown storage kind: {kind!r}')
    return _KIND_FOLDER[kind]


def _ext_ok(ext: str, kind: str) -> bool:
    allowed = ALLOWED_EXTENSIONS.get(kind)
    if allowed is None:
        return True
    return ext.lower() in allowed


# -----------------------------------------------------------------------------
# Quota
# -----------------------------------------------------------------------------
def check_quota(size_bytes: int, domain_id: int = None):
    """Return (ok, message). ok=False blocks the upload.
    `message` is a user-facing reason on failure."""
    # Global disk-fullness gate -- runs before the tenant quota check so
    # an out-of-space disk produces a single clear message rather than a
    # spurious quota error. The disk_monitor module keeps the snapshot
    # fresh on a 5-minute periodic; cold starts (no snapshot yet) skip
    # this check rather than refuse uploads.
    try:
        import disk_monitor
        blocking, used_pct = disk_monitor.is_blocking_uploads()
        if blocking:
            return False, (f'Disk is {used_pct:.1f}% full; uploads are '
                           'temporarily disabled until space is freed.')
    except Exception:
        # Never let monitoring problems break uploads -- fall through.
        pass

    did = domain_id if domain_id is not None else current_domain_id()
    if did is None:
        return False, 'no tenant context'
    with bypass_tenant_filter():
        d = db.session.get(Domain, did)
    if d is None:
        return False, 'tenant not found'
    quota = d.storage_quota_bytes
    if quota is None or quota <= 0:
        return True, None
    if (d.storage_used_bytes or 0) + size_bytes > quota:
        return False, (f'Storage quota exceeded: '
                       f'{(d.storage_used_bytes or 0)} + {size_bytes} > {quota} bytes')
    return True, None


def recompute_used(domain_id: int) -> int:
    """Recalculate Domain.storage_used_bytes from SUM(Media.file_size) for
    that tenant. Returns the new total. Used by housekeeping/admin tools."""
    with bypass_tenant_filter():
        total = (db.session.query(func.coalesce(func.sum(Media.file_size), 0))
                 .filter(Media.domain_id == domain_id).scalar()) or 0
        d = db.session.get(Domain, domain_id)
        if d is not None:
            d.storage_used_bytes = int(total)
            db.session.commit()
    return int(total)


# -----------------------------------------------------------------------------
# Save / delete / serve
# -----------------------------------------------------------------------------
def save_upload(file_storage, kind: str, domain_id: int = None) -> StoredFile:
    """Save a Werkzeug FileStorage. `kind` is 'image' | 'video' | 'misc'.
    Generates a uuid filename and returns a StoredFile."""
    sub = _ensure_kind(kind)
    did = domain_id if domain_id is not None else current_domain_id()
    if did is None:
        raise RuntimeError('save_upload requires a tenant context')

    original = secure_filename(file_storage.filename or '')
    ext = os.path.splitext(original)[1].lower()
    if not _ext_ok(ext, kind):
        raise ValueError(f'extension {ext!r} not allowed for kind {kind!r}')

    folder = os.path.join(_tenant_root(did), sub)
    os.makedirs(folder, exist_ok=True)
    name = f'{uuid.uuid4().hex}{ext}'
    abs_path = os.path.join(folder, name)
    file_storage.save(abs_path)
    size = os.path.getsize(abs_path)

    # Quota check AFTER write keeps logic simple but means we may write a
    # file just to delete it. For Phase 1 this is acceptable; route
    # handlers that care about pre-flight should call check_quota() with
    # the Content-Length header before save_upload().
    ok, msg = check_quota(0, did)   # quota with 0 means "is the tenant already over?"
    # We don't rollback the file here -- it's already counted via the event
    # listener once the Media row is inserted. The actual block happens when
    # the Media row is created (see install_storage_accounting).

    rel = f'd{int(did)}/{sub}/{name}'
    mime = file_storage.content_type or mimetypes.guess_type(abs_path)[0]
    return StoredFile(rel_path=rel, abs_path=abs_path, size=size, mime=mime)


def save_bytes(data: bytes, kind: str, ext: str, domain_id: int = None,
               name_hint: str | None = None) -> StoredFile:
    """Save raw bytes with a given extension. Used for downloads from URL."""
    sub = _ensure_kind(kind)
    if not ext.startswith('.'):
        ext = '.' + ext
    if not _ext_ok(ext, kind):
        raise ValueError(f'extension {ext!r} not allowed for kind {kind!r}')

    did = domain_id if domain_id is not None else current_domain_id()
    if did is None:
        raise RuntimeError('save_bytes requires a tenant context')

    folder = os.path.join(_tenant_root(did), sub)
    os.makedirs(folder, exist_ok=True)
    name = f'{uuid.uuid4().hex}{ext.lower()}'
    abs_path = os.path.join(folder, name)
    with open(abs_path, 'wb') as f:
        f.write(data)
    rel = f'd{int(did)}/{sub}/{name}'
    return StoredFile(rel_path=rel, abs_path=abs_path,
                      size=os.path.getsize(abs_path),
                      mime=mimetypes.guess_type(abs_path)[0])


def open_writer(kind: str, ext: str, domain_id: int = None):
    """Streaming alternative to save_bytes: yields (abs_path, rel_path, fh).
    Caller writes to fh in chunks then closes it. Usage:

        with open_writer('video', '.mp4') as (abs_p, rel_p, fh):
            for chunk in source:
                fh.write(chunk)
        # rel_p now points at the saved file; size = os.path.getsize(abs_p)
    """
    sub = _ensure_kind(kind)
    if not ext.startswith('.'):
        ext = '.' + ext
    if not _ext_ok(ext, kind):
        raise ValueError(f'extension {ext!r} not allowed for kind {kind!r}')
    did = domain_id if domain_id is not None else current_domain_id()
    if did is None:
        raise RuntimeError('open_writer requires a tenant context')

    folder = os.path.join(_tenant_root(did), sub)
    os.makedirs(folder, exist_ok=True)
    name = f'{uuid.uuid4().hex}{ext.lower()}'
    abs_path = os.path.join(folder, name)
    rel = f'd{int(did)}/{sub}/{name}'

    class _Ctx:
        def __enter__(self_):
            self_._fh = open(abs_path, 'wb')
            return abs_path, rel, self_._fh
        def __exit__(self_, *exc):
            try:
                self_._fh.close()
            except Exception:
                pass
    return _Ctx()


def reserve_path(kind: str, ext: str, domain_id: int = None) -> tuple[str, str]:
    """Allocate (abs_path, rel_path) without writing anything. Useful when a
    third-party tool (e.g. ffmpeg, screenshotter) writes directly to disk."""
    sub = _ensure_kind(kind)
    if not ext.startswith('.'):
        ext = '.' + ext
    did = domain_id if domain_id is not None else current_domain_id()
    if did is None:
        raise RuntimeError('reserve_path requires a tenant context')
    folder = os.path.join(_tenant_root(did), sub)
    os.makedirs(folder, exist_ok=True)
    name = f'{uuid.uuid4().hex}{ext.lower()}'
    return os.path.join(folder, name), f'd{int(did)}/{sub}/{name}'


def delete(rel_path: str) -> bool:
    """Delete a stored file by its uploads-relative path. Sandbox-checked.
    Returns True iff a file was removed."""
    abs_p = _resolve(rel_path)
    if abs_p is None or not os.path.exists(abs_p):
        return False
    try:
        os.remove(abs_p)
        return True
    except OSError as e:
        logger.warning(f'storage.delete({rel_path!r}) failed: {e}')
        return False


def absolute_path(rel_path: str) -> str | None:
    """Public sandbox-checked resolver. Returns absolute path or None."""
    return _resolve(rel_path)


def serve_url(rel_path: str) -> str | None:
    """URL the browser/display should fetch the file from. Unsigned --
    use signed_url() for new code; this remains for back-compat."""
    if not rel_path:
        return None
    return url_for('media.uploaded_file', filename=rel_path)


# -----------------------------------------------------------------------------
# Signed URLs - Phase 2 / Task D11
#
# A signed URL embeds an HMAC over (rel_path, expiry) using
# settings['security.signing_key'] as the secret. The /uploads/<path> route
# accepts an unsigned request only for legacy paths (no tenant prefix);
# anything under d<N>/ requires either:
#   * a valid sig + e (expiry) query string, OR
#   * a session whose tenant matches the file's tenant, OR
#   * a superadmin session.
#
# This lets us hand short-lived URLs to display players (which fetch over
# plain HTTP without cookies) without exposing tenant files to the public
# internet, and removes the unauthenticated read fallback we documented in
# Phase 1.
# -----------------------------------------------------------------------------
import hmac as _hmac
import hashlib as _hashlib
import time as _time

# 1 hour matches typical playlist refresh; enough headroom that a slow
# image transfer won't 403 mid-stream, short enough that a leaked URL
# isn't a long-term problem.
SIGNED_URL_TTL_DEFAULT = 3600
# Display players may run one playlist for days; use a long TTL so signed
# /uploads/ links embedded in the playlist payload do not 403 mid-cycle.
SIGNED_URL_TTL_PLAYER = 7 * 24 * 3600


def _signing_key() -> bytes:
    """Fetch the HMAC signing secret from system settings. Returns bytes
    suitable for hmac.new(). Raises RuntimeError if the key is missing,
    which should never happen on a bootstrapped install."""
    import settings as _settings
    key = _settings.effective_value('security.signing_key')
    if not key:
        raise RuntimeError('security.signing_key is not set; run bootstrap')
    return key.encode('utf-8') if isinstance(key, str) else key


def _sign(rel_path: str, expiry: int) -> str:
    """Return the URL-safe HMAC-SHA256 signature for (rel_path, expiry).
    rel_path is normalized to forward slashes so platform differences
    don't break verification."""
    norm = rel_path.replace('\\', '/').lstrip('/')
    msg = f'{norm}|{expiry}'.encode('utf-8')
    digest = _hmac.new(_signing_key(), msg, _hashlib.sha256).digest()
    # Base64 url-safe without padding (shorter URLs).
    import base64
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def signed_url(rel_path: str, ttl_seconds: int = None,
               external: bool = True) -> str | None:
    """Return an `/uploads/<rel_path>?e=<expiry>&sig=<sig>` URL valid for
    `ttl_seconds`. Use `external=False` when embedding in same-origin HTML.

    Returns None for falsy paths or external (webpage) URLs."""
    if not rel_path:
        return None
    # Webpage media uses an external URL string in file_path; never sign.
    if rel_path.startswith(('http://', 'https://')):
        return rel_path
    ttl = ttl_seconds if ttl_seconds is not None else SIGNED_URL_TTL_DEFAULT
    expiry = int(_time.time()) + int(ttl)
    sig = _sign(rel_path, expiry)
    return url_for('media.uploaded_file',
                   filename=rel_path.replace('\\', '/').lstrip('/'),
                   e=expiry, sig=sig, _external=external)


def verify_signature(rel_path: str, expiry, sig: str) -> bool:
    """Constant-time check of a (rel_path, expiry, sig) triple. Returns
    True iff the signature matches AND the URL hasn't expired. Never
    raises -- malformed input returns False."""
    if not (rel_path and expiry and sig):
        return False
    try:
        exp_int = int(expiry)
    except (TypeError, ValueError):
        return False
    if exp_int < int(_time.time()):
        return False
    try:
        expected = _sign(rel_path, exp_int)
    except RuntimeError:
        return False
    # constant-time comparison
    return _hmac.compare_digest(expected, sig)


def is_tenant_path(rel_path: str, domain_id: int = None) -> bool:
    """True iff rel_path lives under the given domain's storage tree.
    Used by uploaded_file() to enforce per-tenant access on serves."""
    did = domain_id if domain_id is not None else current_domain_id()
    if did is None or not rel_path:
        return False
    prefix = f'd{int(did)}/'
    return rel_path.replace('\\', '/').startswith(prefix)


def tenant_for_path(rel_path: str) -> int | None:
    """Extract the domain_id N from a `dN/...` path, or None for legacy."""
    if not rel_path:
        return None
    fwd = rel_path.replace('\\', '/').lstrip('/')
    if not fwd.startswith('d') or '/' not in fwd:
        return None
    head = fwd.split('/', 1)[0]
    try:
        return int(head[1:])
    except ValueError:
        return None


# -----------------------------------------------------------------------------
# Storage accounting (auto-update Domain.storage_used_bytes)
# -----------------------------------------------------------------------------
_ACCOUNTING_INSTALLED = False


def install_storage_accounting(_db):
    """Wire SQLAlchemy events so Domain.storage_used_bytes stays in sync
    with sum(Media.file_size) for the tenant. Call once at startup."""
    global _ACCOUNTING_INSTALLED
    if _ACCOUNTING_INSTALLED:
        return

    @event.listens_for(Session, 'before_flush')
    def _track_media_storage(session, flush_context, instances):
        # Build a per-domain delta from new/dirty/deleted Media instances.
        deltas = {}
        for obj in session.new:
            if isinstance(obj, Media) and obj.domain_id is not None:
                deltas[obj.domain_id] = deltas.get(obj.domain_id, 0) + int(obj.file_size or 0)
        for obj in session.deleted:
            if isinstance(obj, Media) and obj.domain_id is not None:
                deltas[obj.domain_id] = deltas.get(obj.domain_id, 0) - int(obj.file_size or 0)
        for obj in session.dirty:
            if not isinstance(obj, Media):
                continue
            insp = db.inspect(obj)
            hist = insp.attrs.file_size.history
            if not hist.has_changes():
                continue
            old = (hist.deleted[0] if hist.deleted else 0) or 0
            new = (hist.added[0]   if hist.added   else 0) or 0
            if obj.domain_id is not None:
                deltas[obj.domain_id] = deltas.get(obj.domain_id, 0) + (int(new) - int(old))

        if not deltas:
            return

        for did, delta in deltas.items():
            d = session.get(Domain, did)
            if d is None:
                continue
            d.storage_used_bytes = max(0, int(d.storage_used_bytes or 0) + int(delta))
            # Quota check on net positive deltas.
            if delta > 0 and d.storage_quota_bytes and d.storage_used_bytes > d.storage_quota_bytes:
                raise RuntimeError(
                    f'Storage quota exceeded for domain {d.slug!r}: '
                    f'{d.storage_used_bytes} > {d.storage_quota_bytes} bytes'
                )

    _ACCOUNTING_INSTALLED = True
