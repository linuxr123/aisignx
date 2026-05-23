import os
import uuid
import hashlib
import json
import threading
from datetime import datetime
from urllib.parse import urlparse
from flask import Blueprint, render_template, request, jsonify, send_from_directory, abort, url_for, g, current_app
from flask_login import login_required, current_user
from models import db, Media, Display, ApiToken, PlaylistItem, Playlist, SystemSetting
from thumbnail_utils import create_image_thumbnail, create_video_thumbnail, create_webpage_thumbnail
from logging_config import logger
import requests
import mimetypes

import utils
import storage
from tenant_filter import current_domain_id, bypass_tenant_filter
from audit import audit
from permissions import require_permission
from media_duration import probe_video_metadata, whole_seconds, detected_media_duration

media_bp = Blueprint('media', __name__)


def _apply_video_metadata(media, abs_path, explicit_duration=None):
    """Populate detected video metadata and default duration."""
    if not media or media.media_type != 'video':
        return
    meta = probe_video_metadata(abs_path)
    media.duration_seconds = meta.get('duration_seconds')
    media.width = meta.get('width')
    media.height = meta.get('height')
    media.codec = meta.get('codec')
    media.bitrate_bps = meta.get('bitrate_bps')
    if explicit_duration is not None:
        try:
            explicit = int(explicit_duration)
        except (TypeError, ValueError):
            explicit = 0
        if explicit > 0:
            media.duration = explicit
            return
    if media.duration_seconds and media.duration_seconds > 0:
        media.duration = whole_seconds(media.duration_seconds)

# Serve uploaded files (images, videos, thumbnails)
@media_bp.route('/uploads/<path:filename>')
def uploaded_file(filename):
    """Serve a file from UPLOAD_FOLDER with layered access control.

    Authorization, in order of precedence (first match wins):
      1. Valid HMAC signature (?e=&sig=) -- the standard path for display
         players, which fetch over plain HTTP without cookies.
      2. Authenticated session whose active tenant matches the file's
         tenant prefix -- the standard path for the admin UI's <img> tags.
      3. Superadmin session -- can read any tenant's files.
      4. Legacy paths (no d<N>/ prefix) -- served unconditionally for
         back-compat with installs that haven't run the Phase 1 backfill.

    Anything else gets 403. The unauthenticated tenant-file read path that
    existed in Phase 1 is now closed.
    """
    safe_path = storage.absolute_path(filename)
    if safe_path is None or not os.path.isfile(safe_path):
        abort(404)

    fwd = filename.replace('\\', '/').lstrip('/')
    file_did = storage.tenant_for_path(fwd)

    # Legacy (no tenant prefix) -- back-compat passthrough.
    if file_did is None:
        return send_from_directory(os.path.dirname(safe_path),
                                   os.path.basename(safe_path))

    # 1) Signed URL: short-circuits cookie/session checks. This is the
    # only mechanism that works for unauthenticated display players.
    sig = request.args.get('sig')
    expiry = request.args.get('e')
    if sig and expiry and storage.verify_signature(fwd, expiry, sig):
        return send_from_directory(os.path.dirname(safe_path),
                                   os.path.basename(safe_path))

    # 2/3) Session-based access.
    if getattr(current_user, 'is_superadmin', False):
        return send_from_directory(os.path.dirname(safe_path),
                                   os.path.basename(safe_path))
    if current_domain_id() == file_did:
        return send_from_directory(os.path.dirname(safe_path),
                                   os.path.basename(safe_path))

    abort(403)

# Get a single media item
@media_bp.route('/api/media/<int:media_id>', methods=['GET'])
@utils.api_auth_required(['media:read'])
@require_permission('media.read')
def api_get_media_item(media_id):
    # Optionally enforce token binding, if any
    # err = _enforce_media_binding_or_403(media_id)
    # if err: return err

    media = Media.query.get_or_404(media_id)
    return jsonify({'status': 'success', 'media': media.to_dict()})

# List all media items
@media_bp.route('/api/media', methods=['GET'])
@utils.api_auth_required(['media:read'])
@require_permission('media.read')
def api_list_media():
    q = Media.query
    tok = getattr(g, 'api_token', None)
    if tok and tok.media_id:
        q = q.filter(Media.id == tok.media_id)
    else:
        media_type = request.args.get('type')
        name = request.args.get('name')
        external_id = request.args.get('external_id')
        tag = request.args.get('tag')
        folder = request.args.get('folder')
        if media_type:
            q = q.filter_by(media_type=media_type)
        if name:
            q = q.filter(Media.name.ilike(f"%{name}%"))
        if external_id:
            q = q.filter_by(external_id=external_id)
        if folder is not None:
            # Two modes: exact folder, or a "tree" prefix when ?recursive=1.
            f = _normalise_folder(folder)
            if request.args.get('recursive', '').lower() in ('1', 'true', 'yes'):
                if f == '':
                    pass    # root + recursive = no folder filter
                else:
                    q = q.filter(db.or_(Media.folder == f,
                                        Media.folder.like(f + '/%')))
            else:
                if f == '':
                    q = q.filter(db.or_(Media.folder == '',
                                        Media.folder.is_(None)))
                else:
                    q = q.filter(Media.folder == f)
        if tag:
            # Tag column is a comma-joined CSV; match by surrounding commas
            # so "promo" doesn't accidentally match "promo-2025".
            t = tag.strip().lower()
            if t:
                wrapped = db.func.lower(
                    db.func.coalesce(Media.tags, '').op('||')(',')
                ).op('||')('')
                # Build  ',' || lower(coalesce(tags,'')) || ','  LIKE  '%,t,%'
                expr = db.literal(',').op('||')(
                    db.func.lower(db.func.coalesce(Media.tags, ''))
                ).op('||')(',')
                q = q.filter(expr.like(f'%,{t},%'))
    items = q.order_by(Media.created_at.desc()).all()
    return jsonify({
        'status': 'success',
        'media': [
            {
                **m.to_dict(),  # unpack your existing fields
                'thumbnail_url': storage.signed_url(m.thumbnail_path, external=False) if m.thumbnail_path else None
            }
            for m in items
        ]
    })


# ──────────────────────────────────────────────────────────────────────────────
# Folder browsing: lets media tools point at a real directory under uploads/
# instead of picking individual Media rows.
#
# Returns subdirectories of UPLOAD_FOLDER (or a relative path within it). We
# explicitly DO NOT walk system directories: paths are sandboxed to UPLOAD_FOLDER
# the same way uploaded_file() is.
# ──────────────────────────────────────────────────────────────────────────────
_IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg'}

def _safe_uploads_subdir(rel_path: str) -> str | None:
    """Resolve `rel_path` against UPLOAD_FOLDER and return the absolute dir
    if it stays inside the sandbox and exists. Otherwise None."""
    uploads = os.path.abspath(current_app.config['UPLOAD_FOLDER'])
    rel_path = (rel_path or '').strip().strip('/').strip('\\')
    target = os.path.abspath(os.path.join(uploads, rel_path))
    if not target.startswith(uploads):
        return None
    if not os.path.isdir(target):
        return None
    return target


@media_bp.route('/api/media/folders', methods=['GET'])
@login_required
@require_permission('media.read')
def api_list_folders():
    """List subdirectories under UPLOAD_FOLDER (or under a given relative path).

    Query params:
      path     -- relative path to browse, default '' (the uploads root)

    Response:
      { status: 'success',
        path:   'images/vacation',
        parent: 'images',
        folders: [ { name, rel_path, image_count } ... ] }
    """
    rel = request.args.get('path', '')
    abs_dir = _safe_uploads_subdir(rel)
    if abs_dir is None:
        return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 400

    folders = []
    try:
        for entry in sorted(os.listdir(abs_dir)):
            full = os.path.join(abs_dir, entry)
            if not os.path.isdir(full):
                continue
            # Skip hidden + thumbnail caches so the picker stays clean.
            if entry.startswith('.') or entry in ('thumbnails', '__pycache__'):
                continue
            # Cheap image count (top level only -- recursion is handled when
            # the plugin actually loads the folder, no need to count deeply).
            try:
                count = sum(
                    1 for f in os.listdir(full)
                    if os.path.splitext(f)[1].lower() in _IMAGE_EXT
                    and os.path.isfile(os.path.join(full, f))
                )
            except OSError:
                count = 0
            child_rel = os.path.join(rel, entry).replace('\\', '/').strip('/')
            folders.append({
                'name': entry,
                'rel_path': child_rel,
                'image_count': count,
            })
    except OSError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

    parent = os.path.dirname(rel.strip('/').replace('\\', '/'))
    return jsonify({
        'status':  'success',
        'path':    rel.strip('/').replace('\\', '/'),
        'parent':  parent,
        'folders': folders,
    })


@media_bp.route('/api/media/folder_contents', methods=['GET'])
@login_required
@require_permission('media.read')
def api_folder_contents():
    """Return all image files inside a given folder under UPLOAD_FOLDER.

    Query params:
      path       -- required relative folder under uploads/
      recursive  -- '1' to include subfolders (default off)

    Response items mirror the shape of /api/media so the same renderer can
    consume both: each item has id (synthetic, prefixed `f:` so it can't
    collide with a real Media row id), name, file_path, thumbnail_url.
    """
    rel = request.args.get('path', '')
    recursive = request.args.get('recursive', '0') in ('1', 'true', 'yes')
    abs_dir = _safe_uploads_subdir(rel)
    if abs_dir is None:
        return jsonify({'status': 'error', 'message': 'Invalid folder path'}), 400

    uploads = os.path.abspath(current_app.config['UPLOAD_FOLDER'])
    items = []
    walker = os.walk(abs_dir) if recursive else [(abs_dir, [], os.listdir(abs_dir))]
    for dirpath, _dirs, files in walker:
        for fname in sorted(files):
            ext = os.path.splitext(fname)[1].lower()
            if ext not in _IMAGE_EXT:
                continue
            full = os.path.join(dirpath, fname)
            if not os.path.isfile(full):
                continue
            rel_file = os.path.relpath(full, uploads).replace('\\', '/')
            items.append({
                # Synthetic id so the multi-select renderer can include
                # folder items alongside Media-table items without
                # primary-key collisions.
                'id':            'f:' + rel_file,
                'name':          fname,
                'file_path':     rel_file,
                'filename':      rel_file,   # back-compat with media renderer
                'thumbnail_url': storage.signed_url(rel_file, external=False),
                'media_type':    'image',
            })
    return jsonify({
        'status': 'success',
        'path':   rel.strip('/').replace('\\', '/'),
        'count':  len(items),
        'media':  items,    # named 'media' so client renderer is reusable
    })


# Upload new media (image/video)
@media_bp.route('/api/media', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_upload_media():
    tok = getattr(g, 'api_token', None)
    if tok and tok.media_id:
        return jsonify({
            'status': 'error',
            'message': 'This token is restricted to a single media item and cannot create new media.'
        }), 403

    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'}), 400

    file_ext = os.path.splitext(file.filename)[1].lower()
    if file_ext in ['.jpg', '.jpeg', '.png', '.gif']:
        kind = 'image'
    elif file_ext in ['.mp4', '.webm', '.ogg']:
        kind = 'video'
    else:
        return jsonify({'status': 'error', 'message': 'Unsupported file type'}), 400

    # Pre-flight quota check using Content-Length when available.
    declared = request.content_length or 0
    ok, msg = storage.check_quota(declared)
    if not ok:
        return jsonify({'status': 'error', 'message': msg}), 413

    try:
        stored = storage.save_upload(file, kind)
    except (ValueError, RuntimeError) as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    # Compute SHA-256 of the stored bytes for dedupe + integrity tracking.
    checksum = None
    try:
        h = hashlib.sha256()
        with open(stored.abs_path, 'rb') as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b''):
                h.update(chunk)
        checksum = h.hexdigest()
    except OSError as e:
        logger.warning(f"checksum compute failed for {stored.rel_path}: {e}")

    # Tenant-scoped dedupe: if another media row in this tenant has the
    # same checksum, drop the new copy and point the operator at the
    # existing one. Honors ?allow_duplicate=1 for explicit overrides.
    allow_dup = (request.form.get('allow_duplicate', '').lower()
                 in ('1', 'true', 'yes'))
    if checksum and not allow_dup:
        existing = Media.query.filter_by(checksum_sha256=checksum).first()
        if existing is not None:
            storage.delete(stored.rel_path)
            return jsonify({
                'status': 'duplicate',
                'message': 'A media item with identical contents already exists.',
                'existing': existing.to_dict(),
            }), 409

    # Generate thumbnail into the tenant's thumbnails folder.
    thumb_abs, thumb_rel = storage.reserve_path('thumbnail', '.png')
    thumb_status = 'ok'
    thumb_when = datetime.utcnow()
    try:
        if kind == 'image':
            create_image_thumbnail(stored.abs_path, thumb_abs)
        else:
            create_video_thumbnail(stored.abs_path, thumb_abs)
    except Exception as e:
        logger.error(f"Thumbnail generation failed: {e}")
        thumb_rel = None
        thumb_status = 'failed'
        thumb_when = None

    explicit_duration = request.form.get('duration')
    media = Media(
        name=request.form.get('name') or os.path.splitext(file.filename)[0],
        filename=os.path.basename(stored.rel_path),
        file_path=stored.rel_path,           # NOTE: rel_path now, not abs
        media_type=kind,
        mime_type=stored.mime,
        duration=request.form.get('duration', 10, type=int),
        description=request.form.get('description', ''),
        file_size=stored.size,
        checksum_sha256=checksum,
        thumbnail_path=thumb_rel,
        thumbnail_status=thumb_status,
        thumbnail_generated_at=thumb_when,
    )
    if kind == 'video':
        _apply_video_metadata(media, stored.abs_path, explicit_duration=explicit_duration)
    # Optional folder placement at upload time. Invalid folder strings
    # are dropped silently rather than failing the upload -- the file is
    # already on disk, the operator can move it later from the UI.
    raw_folder = request.form.get('folder')
    if raw_folder is not None:
        try:
            media.folder = _normalise_folder(raw_folder)
        except ValueError:
            media.folder = ''
    db.session.add(media)
    try:
        db.session.commit()
    except RuntimeError as e:
        # Quota tripped at flush time -- delete the orphaned bytes.
        db.session.rollback()
        storage.delete(stored.rel_path)
        if thumb_rel:
            storage.delete(thumb_rel)
        return jsonify({'status': 'error', 'message': str(e)}), 413

    audit('media.upload', target_type='media', target_id=str(media.id),
          payload={'name': media.name, 'kind': kind,
                   'size': stored.size, 'filename': file.filename})

    return jsonify({
        'status': 'success',
        'message': 'Media uploaded successfully',
        'media': media.to_dict()
    })

# Add webpage as media
@media_bp.route('/api/media/webpage', methods=['POST'])
@login_required
@require_permission('media.upload')
def api_add_webpage():
    data = request.json
    if not data or not data.get('url'):
        return jsonify({
            'status': 'error',
            'message': 'URL is required'
        }), 400

    # Auto-generate a name from the URL if not provided
    url = data.get('url', '').strip()
    name = (data.get('name') or '').strip()
    if not name:
        try:
            name = urlparse(url).hostname or url
        except Exception:
            name = url

    # Reserve a per-tenant thumbnail path; the file itself isn't created yet
    # (the background thread renders it). Storing the relative path on the
    # Media row up front means clients can resolve a thumbnail URL once the
    # file lands; until then we leave thumbnail_path NULL.
    thumb_abs, thumb_rel = storage.reserve_path('thumbnail', '.png')

    media = Media(
        name=name,
        filename=f"webpage_{uuid.uuid4().hex}",
        file_path=url,
        media_type='webpage',
        mime_type='text/html',
        duration=data.get('duration', 30),
        description=data.get('description', ''),
        file_size=0,
        meta_data={
            'url': url,
            'refresh_interval': data.get('refresh_interval', 0),
            'scrolling': data.get('scrolling', True)
        },
        thumbnail_path=None
    )
    db.session.add(media)
    db.session.commit()
    media_id = media.id
    audit('media.webpage_create', target_type='media', target_id=str(media_id),
          payload={'name': name, 'url': url})

    app = current_app._get_current_object()
    def _gen_thumb():
        try:
            create_webpage_thumbnail(url, thumb_abs)
            with app.app_context():
                with bypass_tenant_filter():    # tenant-ok: bg thread
                    m = db.session.get(Media, media_id)
                    if m:
                        m.thumbnail_path = thumb_rel
                        db.session.commit()
        except Exception as e:
            logger.error(f"Webpage thumbnail generation failed: {e}")
    threading.Thread(target=_gen_thumb, daemon=True).start()

    return jsonify({
        'status': 'success',
        'message': 'Webpage added successfully',
        'media': media.to_dict()
    })

@media_bp.route('/api/media/from_url', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_media_from_url():
    tok = getattr(g, 'api_token', None)
    if tok and tok.media_id:
        return jsonify({
            'status': 'error',
            'message': 'This token is restricted to a single media item and cannot create new media.'
        }), 403

    data = request.json or {}
    src = data.get('source_url')
    if not src:
        return jsonify({'status': 'error', 'message': 'source_url is required'}), 400

    name = data.get('name') or os.path.basename(urlparse(src).path) or 'Untitled'
    description = data.get('description', '')
    duration = int(data.get('duration', 10))
    external_id = data.get('external_id')

    # Download
    try:
        r = requests.get(src, stream=True, timeout=60)
        r.raise_for_status()
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'download failed: {e}'}), 400

    ct = r.headers.get('Content-Type', '')
    ext = mimetypes.guess_extension(ct.split(';')[0]) or os.path.splitext(urlparse(src).path)[1].lower()
    if ext in ('.jpg', '.jpeg', '.png', '.gif'):
        kind = 'image'
    elif ext in ('.mp4', '.webm', '.ogg'):
        kind = 'video'
    else:
        return jsonify({'status': 'error', 'message': f'Unsupported file type: {ext or ct}'}), 400

    # Pre-flight quota check using Content-Length when available.
    cl = int(r.headers.get('Content-Length') or 0)
    if cl:
        ok, msg = storage.check_quota(cl)
        if not ok:
            return jsonify({'status': 'error', 'message': msg}), 413

    # Stream into a tenant-scoped path.
    try:
        with storage.open_writer(kind, ext) as (abs_path, rel_path, fh):
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    fh.write(chunk)
    except (ValueError, RuntimeError) as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    size = os.path.getsize(abs_path)

    # Thumbnail
    thumb_abs, thumb_rel = storage.reserve_path('thumbnail', '.png')
    try:
        if kind == 'image':
            create_image_thumbnail(abs_path, thumb_abs)
        else:
            create_video_thumbnail(abs_path, thumb_abs)
    except Exception as e:
        logger.error(f"Thumbnail generation failed: {e}")
        thumb_rel = None

    media = Media(
        name=name,
        filename=os.path.basename(rel_path),
        file_path=rel_path,
        media_type=kind,
        mime_type=ct or mimetypes.guess_type(abs_path)[0],
        duration=duration,
        description=description,
        file_size=size,
        thumbnail_path=thumb_rel,
        external_id=external_id
    )
    if kind == 'video':
        _apply_video_metadata(media, abs_path, explicit_duration=data.get('duration'))
    db.session.add(media)
    try:
        db.session.commit()
    except RuntimeError as e:
        db.session.rollback()
        storage.delete(rel_path)
        if thumb_rel:
            storage.delete(thumb_rel)
        return jsonify({'status': 'error', 'message': str(e)}), 413

    audit('media.from_url', target_type='media', target_id=str(media.id),
          payload={'name': name, 'kind': kind, 'size': size, 'source_url': src})
    return jsonify({'status': 'success', 'media': media.to_dict()})

@media_bp.route('/api/media/<int:media_id>/replace_by_url', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.edit')
def api_replace_media_by_url(media_id):
    err = utils._enforce_media_binding_or_403(media_id)
    if err: return err

    media = Media.query.get_or_404(media_id)
    if media.media_type == 'webpage':
        return jsonify({'status': 'error', 'message': 'Cannot replace file for webpage media'}), 400
    data = request.json or {}
    src = data.get('source_url')
    if not src:
        return jsonify({'status': 'error', 'message': 'source_url is required'}), 400

    try:
        r = requests.get(src, stream=True, timeout=60)
        r.raise_for_status()
    except Exception as e:
        return jsonify({'status': 'error', 'message': f'download failed: {e}'}), 400

    ct = r.headers.get('Content-Type', '')
    ext = mimetypes.guess_extension(ct.split(';')[0]) or os.path.splitext(urlparse(src).path)[1].lower()

    if media.media_type == 'image' and ext not in ('.jpg', '.jpeg', '.png', '.gif'):
        return jsonify({'status': 'error', 'message': 'Replacement must be an image'}), 400
    if media.media_type == 'video' and ext not in ('.mp4', '.webm', '.ogg'):
        return jsonify({'status': 'error', 'message': 'Replacement must be a video'}), 400

    old_file_rel  = media.file_path
    old_thumb_rel = media.thumbnail_path
    kind = media.media_type

    cl = int(r.headers.get('Content-Length') or 0)
    if cl:
        ok, msg = storage.check_quota(cl)
        if not ok:
            return jsonify({'status': 'error', 'message': msg}), 413

    try:
        with storage.open_writer(kind, ext) as (new_abs, new_rel, fh):
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    fh.write(chunk)
    except (ValueError, RuntimeError) as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    thumb_abs, thumb_rel = storage.reserve_path('thumbnail', '.png')
    try:
        if kind == 'image':
            create_image_thumbnail(new_abs, thumb_abs)
        else:
            create_video_thumbnail(new_abs, thumb_abs)
    except Exception as e:
        logger.error(f"Thumbnail regeneration failed: {e}")
        thumb_rel = old_thumb_rel

    media.filename       = os.path.basename(new_rel)
    media.file_path      = new_rel
    media.mime_type      = ct or mimetypes.guess_type(new_abs)[0]
    media.file_size      = os.path.getsize(new_abs)
    media.thumbnail_path = thumb_rel
    media.updated_at     = datetime.now()
    if media.media_type == 'video':
        _apply_video_metadata(media, new_abs)
    try:
        db.session.commit()
    except RuntimeError as e:
        db.session.rollback()
        storage.delete(new_rel)
        if thumb_rel and thumb_rel != old_thumb_rel:
            storage.delete(thumb_rel)
        return jsonify({'status': 'error', 'message': str(e)}), 413

    if old_file_rel and old_file_rel != new_rel:
        storage.delete(old_file_rel)
    if old_thumb_rel and old_thumb_rel != thumb_rel:
        storage.delete(old_thumb_rel)

    audit('media.replace_by_url', target_type='media', target_id=str(media.id),
          payload={'source_url': src, 'size': media.file_size})
    return jsonify({'status': 'success', 'media': media.to_dict()})

# Update media metadata
@media_bp.route('/api/media/<int:media_id>', methods=['PUT'])
@utils.api_auth_required(['media:write'])
@require_permission('media.edit')
def api_update_media(media_id):
    media = Media.query.get_or_404(media_id)
    data = request.json
    if data.get('name'):
        media.name = data.get('name')
    if 'description' in data:
        media.description = data.get('description')
    if 'duration' in data:
        if (isinstance(data.get('duration'), str)
                and data.get('duration').strip().lower() == 'detected'):
            if media.media_type == 'video':
                media.duration = detected_media_duration(media)
        else:
            media.duration = data.get('duration')
    # Per-media default video audio behavior. Stored on the media row so it
    # applies wherever the file is played; playlists and individual playlist
    # items can still override at runtime.
    if 'audio_enabled' in data and media.media_type == 'video':
        media.audio_enabled = bool(data.get('audio_enabled'))
    # Webpage-specific metadata
    if media.media_type == 'webpage':
        url_changed = data.get('url') and data.get('url') != media.file_path
        if data.get('url'):
            media.file_path = data.get('url')
        # Regenerate thumbnail if URL changed or thumbnail is missing
        if url_changed or not media.thumbnail_path:
            thumb_abs, thumb_rel = storage.reserve_path('thumbnail', '.png')
            thumb_url = media.file_path
            mid = media.id
            app = current_app._get_current_object()
            def _regen_thumb():
                try:
                    create_webpage_thumbnail(thumb_url, thumb_abs)
                    with app.app_context():
                        with bypass_tenant_filter():    # tenant-ok: bg thread
                            m = db.session.get(Media, mid)
                            if m:
                                m.thumbnail_path = thumb_rel
                                db.session.commit()
                except Exception as e:
                    logger.error(f"Webpage thumbnail regeneration failed: {e}")
            threading.Thread(target=_regen_thumb, daemon=True).start()
        if media.meta_data is None:
            media.meta_data = {}
        if 'refresh_interval' in data:
            media.meta_data['refresh_interval'] = data.get('refresh_interval')
        if 'scrolling' in data:
            media.meta_data['scrolling'] = data.get('scrolling')
    media.updated_at = datetime.now()
    db.session.commit()
    audit('media.update', target_type='media', target_id=str(media.id),
          payload={'name': media.name, 'kind': media.media_type})
    return jsonify({'status': 'success', 'message': 'Media updated successfully', 'media': media.to_dict()})

@media_bp.route('/api/media/<int:media_id>/replace', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.edit')
def api_replace_media_file(media_id):
    """Replace the file content of an existing media item."""
    err = utils._enforce_media_binding_or_403(media_id)
    if err: return err

    media = Media.query.get_or_404(media_id)
    if media.media_type == 'webpage':
        return jsonify({'status': 'error', 'message': 'Cannot replace file for webpage media. Update the URL instead.'}), 400
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file part'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'}), 400
    file_ext = os.path.splitext(file.filename)[1].lower()
    if media.media_type == 'image' and file_ext not in ['.jpg', '.jpeg', '.png', '.gif']:
        return jsonify({'status': 'error', 'message': 'Replacement file must be an image'}), 400
    elif media.media_type == 'video' and file_ext not in ['.mp4', '.webm', '.ogg']:
        return jsonify({'status': 'error', 'message': 'Replacement file must be a video'}), 400

    declared = request.content_length or 0
    ok, msg = storage.check_quota(declared)
    if not ok:
        return jsonify({'status': 'error', 'message': msg}), 413

    old_file_rel  = media.file_path
    old_thumb_rel = media.thumbnail_path

    try:
        stored = storage.save_upload(file, media.media_type)
    except (ValueError, RuntimeError) as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    thumb_abs, thumb_rel = storage.reserve_path('thumbnail', '.png')
    try:
        if media.media_type == 'image':
            create_image_thumbnail(stored.abs_path, thumb_abs)
        else:
            create_video_thumbnail(stored.abs_path, thumb_abs)
    except Exception as e:
        logger.error(f"Thumbnail regeneration failed: {e}")
        thumb_rel = old_thumb_rel

    media.filename       = os.path.basename(stored.rel_path)
    media.file_path      = stored.rel_path
    media.mime_type      = stored.mime
    media.file_size      = stored.size
    media.thumbnail_path = thumb_rel
    media.updated_at     = datetime.now()
    if media.media_type == 'video':
        _apply_video_metadata(media, stored.abs_path)
    try:
        db.session.commit()
    except RuntimeError as e:
        db.session.rollback()
        storage.delete(stored.rel_path)
        if thumb_rel and thumb_rel != old_thumb_rel:
            storage.delete(thumb_rel)
        return jsonify({'status': 'error', 'message': str(e)}), 413

    if old_file_rel and old_file_rel != stored.rel_path:
        storage.delete(old_file_rel)
    if old_thumb_rel and old_thumb_rel != thumb_rel:
        storage.delete(old_thumb_rel)

    logger.info(f"Media file replaced: ID={media.id}, Name={media.name}, Type={media.media_type}")
    audit('media.replace', target_type='media', target_id=str(media.id),
          payload={'name': media.name, 'kind': media.media_type, 'size': media.file_size})
    return jsonify({'status': 'success', 'message': 'Media file replaced successfully', 'media': media.to_dict()})


# Delete media
@media_bp.route('/api/media/<int:media_id>', methods=['DELETE'])
@utils.api_auth_required(['media:write'])
@require_permission('media.delete')
def api_delete_media(media_id):
    media = Media.query.get_or_404(media_id)
    snapshot = {'name': media.name, 'kind': media.media_type,
                'file': media.file_path, 'size': media.file_size}

    # Usage-aware guard: if this media is still referenced by any
    # playlist, refuse the delete unless ?force=1 was passed. Returns
    # 409 with the list of referencing playlists so the operator can
    # decide whether to proceed.
    force = (request.args.get('force', '').lower()
             in ('1', 'true', 'yes'))
    refs = (db.session.query(Playlist.id, Playlist.name)
            .join(PlaylistItem, PlaylistItem.playlist_id == Playlist.id)
            .filter(PlaylistItem.media_id == media_id)
            .distinct()
            .all())
    if refs and not force:
        return jsonify({
            'status': 'in_use',
            'message': f'Media is used by {len(refs)} playlist(s). '
                       'Pass force=1 to delete anyway.',
            'playlists': [{'id': pid, 'name': pname} for pid, pname in refs],
        }), 409

    if media.media_type != 'webpage' and media.file_path:
        # New rows use uploads-relative paths; legacy rows may store absolute
        # paths. storage.delete handles relative; for legacy fall back to the
        # raw os.remove path used before Phase 1.
        if not storage.delete(media.file_path):
            if os.path.isabs(media.file_path) and os.path.exists(media.file_path):
                try:
                    os.remove(media.file_path)
                except OSError as e:
                    logger.error(f"Error deleting legacy file {media.file_path}: {e}")

    if media.thumbnail_path:
        if not storage.delete(media.thumbnail_path):
            # Legacy layout: thumbnail_path was relative to UPLOAD_FOLDER root.
            legacy = os.path.join(current_app.config['UPLOAD_FOLDER'], media.thumbnail_path)
            if os.path.exists(legacy):
                try:
                    os.remove(legacy)
                except OSError as e:
                    logger.error(f"Error deleting legacy thumbnail {legacy}: {e}")

    db.session.delete(media)
    db.session.commit()
    audit('media.delete', target_type='media', target_id=str(media_id),
          payload={**snapshot, 'forced': bool(refs)})
    return jsonify({'status': 'success', 'message': 'Media deleted successfully'})


# Bulk delete: best-effort over a list of media ids. Honors the same
# usage-aware guard as the single-item endpoint and reports per-item
# outcomes so the UI can render a result table.
@media_bp.route('/api/media/bulk-delete', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.delete')
def api_bulk_delete_media():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    force = bool(data.get('force'))
    if not isinstance(ids, list) or not ids:
        return jsonify({'status': 'error', 'message': 'ids[] required'}), 400

    results = []
    for raw in ids:
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            results.append({'id': raw, 'status': 'error',
                            'message': 'invalid id'})
            continue
        m = Media.query.get(mid)
        if m is None:
            results.append({'id': mid, 'status': 'not_found'})
            continue
        refs = (db.session.query(PlaylistItem.id)
                .filter(PlaylistItem.media_id == mid).count())
        if refs and not force:
            results.append({'id': mid, 'status': 'in_use',
                            'playlist_refs': refs})
            continue
        try:
            if m.media_type != 'webpage' and m.file_path:
                storage.delete(m.file_path)
            if m.thumbnail_path:
                storage.delete(m.thumbnail_path)
            db.session.delete(m)
            db.session.commit()
            audit('media.delete', target_type='media', target_id=str(mid),
                  payload={'name': m.name, 'kind': m.media_type,
                           'bulk': True, 'forced': bool(refs)})
            results.append({'id': mid, 'status': 'deleted'})
        except Exception as e:
            db.session.rollback()
            logger.error(f'bulk-delete media {mid} failed: {e}')
            results.append({'id': mid, 'status': 'error',
                            'message': str(e)})
    return jsonify({'status': 'success', 'results': results})


# Tag normaliser: lowercase, strip, dedupe, drop empties, cap each tag
# at 32 chars, and cap the total joined length so it fits the column.
def _normalise_tags(raw):
    if raw is None:
        return None
    if isinstance(raw, str):
        parts = raw.split(',')
    elif isinstance(raw, (list, tuple, set)):
        parts = list(raw)
    else:
        return None
    out = []
    seen = set()
    for p in parts:
        t = str(p).strip().lower()
        if not t or t in seen:
            continue
        if len(t) > 32:
            t = t[:32]
        seen.add(t)
        out.append(t)
    joined = ','.join(out)
    if len(joined) > 480:
        # Truncate to fit; lose trailing tags rather than overflow.
        while out and len(','.join(out)) > 480:
            out.pop()
        joined = ','.join(out)
    return joined


# ──────────────────────────────────────────────────────────────────────────────
# Logical media folders
# ──────────────────────────────────────────────────────────────────────────────
# A "folder" here is purely a metadata grouping stored on Media.folder. It
# does NOT correspond to a directory on disk -- the file lives wherever
# storage.save_upload() put it. This keeps tenant isolation trivial (the
# Media row already carries domain_id) and makes rename/move free.
#
# Path format:
#   - '/'-separated, no leading or trailing slash
#   - empty string == root / "Uncategorised"
#   - segments stripped, control characters refused, max 64 chars each,
#     max depth 8, max total length 240 (column is 255)
_FOLDER_MAX_DEPTH = 8
_FOLDER_MAX_SEG = 64
_FOLDER_MAX_TOTAL = 240
_FOLDER_BAD = set(chr(i) for i in range(0x00, 0x20)) | {'\\', '<', '>', ':', '"', '|', '?', '*'}


_EMPTY_FOLDERS_KEY = 'media.empty_folders'


def _load_empty_folders():
    """Return a set of canonical folder paths the user has created but that
    don't have any media in them yet. Stored per-tenant in SystemSetting as
    a JSON list so empty folders survive page reloads."""
    did = current_domain_id()
    row = SystemSetting.query.filter_by(domain_id=did,
                                        key=_EMPTY_FOLDERS_KEY).first()
    if not row or not row.value:
        return set()
    try:
        data = json.loads(row.value)
        if isinstance(data, list):
            return {str(x) for x in data if x}
    except Exception:
        pass
    return set()


def _save_empty_folders(folders):
    did = current_domain_id()
    row = SystemSetting.query.filter_by(domain_id=did,
                                        key=_EMPTY_FOLDERS_KEY).first()
    payload = json.dumps(sorted({f for f in folders if f}))
    if row is None:
        row = SystemSetting(domain_id=did, key=_EMPTY_FOLDERS_KEY,
                            value=payload, value_type='json')
        db.session.add(row)
    else:
        row.value = payload
        row.value_type = 'json'
    db.session.commit()


def _normalise_folder(raw):
    """Coerce a user-supplied folder path to canonical form. Returns '' for
    root. Raises ValueError on invalid input so callers can 400."""
    if raw is None:
        return ''
    s = str(raw).strip().replace('\\', '/').strip('/')
    if not s:
        return ''
    parts = []
    for seg in s.split('/'):
        seg = seg.strip()
        if not seg or seg in ('.', '..'):
            raise ValueError(f'invalid folder segment: {seg!r}')
        if any(c in _FOLDER_BAD for c in seg):
            raise ValueError(f'folder contains forbidden character: {seg!r}')
        if len(seg) > _FOLDER_MAX_SEG:
            raise ValueError(f'folder segment too long (>{_FOLDER_MAX_SEG}): {seg!r}')
        parts.append(seg)
    if len(parts) > _FOLDER_MAX_DEPTH:
        raise ValueError(f'folder nesting too deep (>{_FOLDER_MAX_DEPTH})')
    out = '/'.join(parts)
    if len(out) > _FOLDER_MAX_TOTAL:
        raise ValueError(f'folder path too long (>{_FOLDER_MAX_TOTAL})')
    return out


@media_bp.route('/api/media/library-folders', methods=['GET'])
@utils.api_auth_required(['media:read'])
@require_permission('media.read')
def api_list_library_folders():
    """List every distinct logical folder in this tenant's media library
    plus per-folder counts. Used by the Media page sidebar tree.

    The route is named 'library-folders' to avoid clashing with the
    existing '/api/media/folders' endpoint, which lists *filesystem*
    sub-directories under uploads/ for the Image-Library plugin.
    """
    rows = db.session.query(Media.folder, db.func.count(Media.id)) \
        .group_by(Media.folder).all()
    counts = {}    # canonical folder -> count for items directly in it
    for raw, n in rows:
        try:
            f = _normalise_folder(raw)
        except ValueError:
            f = ''
        counts[f] = counts.get(f, 0) + int(n)

    # Materialise every ancestor so the tree includes empty parents.
    all_folders = set([''])
    for f in counts.keys():
        if not f:
            continue
        parts = f.split('/')
        for i in range(1, len(parts) + 1):
            all_folders.add('/'.join(parts[:i]))

    # Include user-created empty folders (and their ancestors) so freshly
    # created folders appear in the tree even before any media lives in them.
    for f in _load_empty_folders():
        try:
            f = _normalise_folder(f)
        except ValueError:
            continue
        if not f:
            continue
        parts = f.split('/')
        for i in range(1, len(parts) + 1):
            all_folders.add('/'.join(parts[:i]))

    folders = []
    for f in sorted(all_folders):
        # Recursive count = items whose folder == f or starts with f + '/'
        if f == '':
            recursive = sum(counts.values())
        else:
            recursive = sum(c for k, c in counts.items()
                            if k == f or k.startswith(f + '/'))
        folders.append({
            'path':            f,
            'name':            f.rsplit('/', 1)[-1] if f else '',
            'depth':           f.count('/') + 1 if f else 0,
            'item_count':      counts.get(f, 0),
            'recursive_count': recursive,
        })
    return jsonify({'status': 'success', 'folders': folders})


@media_bp.route('/api/media/folder-create', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_create_folder():
    """Create an empty logical folder for this tenant. The folder will
    appear in the sidebar tree even before any media is moved into it.

    Body: { "folder": "campaigns/winter" }
    """
    data = request.get_json(silent=True) or {}
    try:
        path = _normalise_folder(data.get('folder', ''))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    if path == '':
        return jsonify({'status': 'error',
                        'message': 'folder name required'}), 400
    empties = _load_empty_folders()
    empties.add(path)
    _save_empty_folders(empties)
    audit('media.folder_create', target_type='folder', target_id=path,
          payload={'folder': path})
    return jsonify({'status': 'success', 'folder': path})


@media_bp.route('/api/media/move', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_move_media():
    """Move one or more media items to a folder.

    Body: { "ids": [1, 2, 3], "folder": "campaigns/winter" }
    Pass folder='' to move back to the root.
    """
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'status': 'error', 'message': 'ids required'}), 400
    try:
        target = _normalise_folder(data.get('folder', ''))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    rows = Media.query.filter(Media.id.in_([int(i) for i in ids])).all()
    moved = []
    for m in rows:
        if (m.folder or '') != target:
            old = m.folder or ''
            m.folder = target
            moved.append({'id': m.id, 'from': old, 'to': target})
    db.session.commit()
    if moved:
        audit('media.folder_move', target_type='media',
              target_id=','.join(str(x['id']) for x in moved),
              payload={'folder': target, 'moved': moved})
    return jsonify({'status': 'success', 'moved': moved, 'folder': target})


@media_bp.route('/api/media/folder-rename', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_rename_folder():
    """Rename a folder (and every descendant) in this tenant's library.

    Body: { "from": "campaigns/winter", "to": "campaigns/holiday-2025" }
    """
    data = request.get_json(silent=True) or {}
    try:
        src = _normalise_folder(data.get('from', ''))
        dst = _normalise_folder(data.get('to', ''))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    if src == '':
        return jsonify({'status': 'error', 'message': 'cannot rename root'}), 400
    if dst == '' or dst == src:
        return jsonify({'status': 'error', 'message': 'destination invalid'}), 400
    if dst == src or dst.startswith(src + '/'):
        return jsonify({'status': 'error',
                        'message': 'destination cannot be inside source'}), 400

    rows = Media.query.filter(db.or_(Media.folder == src,
                                     Media.folder.like(src + '/%'))).all()
    affected = 0
    for m in rows:
        cur = m.folder or ''
        if cur == src:
            m.folder = dst
        elif cur.startswith(src + '/'):
            m.folder = dst + cur[len(src):]
        affected += 1
    # Migrate any user-created empty folders too.
    empties = _load_empty_folders()
    new_empties = set()
    changed = False
    for f in empties:
        if f == src:
            new_empties.add(dst); changed = True
        elif f.startswith(src + '/'):
            new_empties.add(dst + f[len(src):]); changed = True
        else:
            new_empties.add(f)
    if changed:
        _save_empty_folders(new_empties)
    db.session.commit()
    if affected:
        audit('media.folder_rename', target_type='folder',
              target_id=src,
              payload={'from': src, 'to': dst, 'affected': affected})
    return jsonify({'status': 'success', 'from': src, 'to': dst,
                    'affected': affected})


@media_bp.route('/api/media/folder-delete', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_delete_folder():
    """Remove a folder by detaching every item back to root (or to a
    specified target). Files on disk are NOT touched.

    Body: { "folder": "campaigns/winter", "into": "" }   # 'into' optional
    """
    data = request.get_json(silent=True) or {}
    try:
        src = _normalise_folder(data.get('folder', ''))
        into = _normalise_folder(data.get('into', ''))
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400
    if src == '':
        return jsonify({'status': 'error', 'message': 'cannot delete root'}), 400
    if into == src or into.startswith(src + '/'):
        return jsonify({'status': 'error',
                        'message': "'into' cannot be inside the folder being deleted"}), 400

    rows = Media.query.filter(db.or_(Media.folder == src,
                                     Media.folder.like(src + '/%'))).all()
    for m in rows:
        cur = m.folder or ''
        if cur == src:
            m.folder = into
        elif cur.startswith(src + '/'):
            # Re-parent descendants under `into` (preserves tree shape).
            tail = cur[len(src):].lstrip('/')
            m.folder = (into + '/' + tail).strip('/') if into else tail
    # Drop the deleted folder (and any descendants) from the user-created
    # empty set as well.
    empties = _load_empty_folders()
    pruned = {f for f in empties
              if not (f == src or f.startswith(src + '/'))}
    if pruned != empties:
        _save_empty_folders(pruned)
    db.session.commit()
    audit('media.folder_delete', target_type='folder', target_id=src,
          payload={'folder': src, 'into': into, 'reparented': len(rows)})
    return jsonify({'status': 'success', 'folder': src, 'into': into,
                    'reparented': len(rows)})


# Bulk tag editor: add and/or remove tags across many media rows in one
# request. Tags are normalised (lowercase, deduped, stripped). Returns
# the resulting tag list per item so the UI can update its chips
# without a second fetch.
@media_bp.route('/api/media/bulk-tag', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_bulk_tag_media():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    add = _normalise_tags(data.get('add') or [])
    remove = _normalise_tags(data.get('remove') or [])
    replace_with = data.get('replace_with')
    if not isinstance(ids, list) or not ids:
        return jsonify({'status': 'error', 'message': 'ids[] required'}), 400
    if replace_with is not None:
        replace_with = _normalise_tags(replace_with) or ''
    add_set = set((add or '').split(',')) - {''}
    remove_set = set((remove or '').split(',')) - {''}

    results = []
    for raw in ids:
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            results.append({'id': raw, 'status': 'error',
                            'message': 'invalid id'})
            continue
        m = Media.query.get(mid)
        if m is None:
            results.append({'id': mid, 'status': 'not_found'})
            continue
        before = [t for t in (m.tags or '').split(',') if t]
        if replace_with is not None:
            after = [t for t in replace_with.split(',') if t]
        else:
            after = list(dict.fromkeys([*before, *add_set]))
            after = [t for t in after if t not in remove_set]
        m.tags = _normalise_tags(after) or ''
        results.append({'id': mid, 'status': 'ok', 'tags': [t for t in m.tags.split(',') if t]})
    db.session.commit()
    audit('media.bulk_tag', target_type='media', target_id='bulk',
          payload={'count': sum(1 for r in results if r['status'] == 'ok'),
                   'add': sorted(add_set), 'remove': sorted(remove_set),
                   'replace': replace_with is not None})
    return jsonify({'status': 'success', 'results': results})


# Bulk rename: apply a find/replace (literal substring) across each
# selected media item's name. Optionally a `prefix` and `suffix` are
# appended after substitution. Empty `find` = no substitution (still
# allows pure prefix/suffix). Returns before/after for each item.
@media_bp.route('/api/media/bulk-rename', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_bulk_rename_media():
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    find = data.get('find') or ''
    repl = data.get('replace') or ''
    prefix = data.get('prefix') or ''
    suffix = data.get('suffix') or ''
    if not isinstance(ids, list) or not ids:
        return jsonify({'status': 'error', 'message': 'ids[] required'}), 400
    if not find and not prefix and not suffix:
        return jsonify({'status': 'error',
                        'message': 'one of find/prefix/suffix required'}), 400

    results = []
    for raw in ids:
        try:
            mid = int(raw)
        except (TypeError, ValueError):
            results.append({'id': raw, 'status': 'error',
                            'message': 'invalid id'})
            continue
        m = Media.query.get(mid)
        if m is None:
            results.append({'id': mid, 'status': 'not_found'})
            continue
        new_name = m.name or ''
        if find:
            new_name = new_name.replace(find, repl)
        new_name = f'{prefix}{new_name}{suffix}'
        new_name = new_name.strip()[:120]
        if not new_name:
            results.append({'id': mid, 'status': 'error',
                            'message': 'rename produced empty name'})
            continue
        if new_name == m.name:
            results.append({'id': mid, 'status': 'unchanged', 'name': m.name})
            continue
        old = m.name
        m.name = new_name
        results.append({'id': mid, 'status': 'renamed',
                        'from': old, 'to': new_name})
    db.session.commit()
    audit('media.bulk_rename', target_type='media', target_id='bulk',
          payload={'count': sum(1 for r in results if r['status'] == 'renamed'),
                   'find': find, 'replace': repl,
                   'prefix': prefix, 'suffix': suffix})
    return jsonify({'status': 'success', 'results': results})


# Single-item update of tags (used by the chip editor in the row).
@media_bp.route('/api/media/<int:media_id>/tags', methods=['PUT'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_set_media_tags(media_id):
    m = Media.query.get_or_404(media_id)
    data = request.get_json(silent=True) or {}
    if 'tags' not in data:
        return jsonify({'status': 'error', 'message': 'tags required'}), 400
    new = _normalise_tags(data['tags']) or ''
    old = m.tags or ''
    m.tags = new
    db.session.commit()
    if old != new:
        audit('media.tags_update', target_type='media', target_id=str(media_id),
              payload={'from': [t for t in old.split(',') if t],
                       'to':   [t for t in new.split(',') if t]})
    return jsonify({'status': 'success',
                    'tags': [t for t in new.split(',') if t]})


# List all distinct tags currently in use in this tenant. Cheap helper
# for autocomplete in the UI; returns counts so the chip cloud can sort
# by popularity.
@media_bp.route('/api/media/tags', methods=['GET'])
@utils.api_auth_required(['media:read'])
@require_permission('media.read')
def api_list_media_tags():
    rows = db.session.query(Media.tags).filter(
        Media.tags.isnot(None), Media.tags != '').all()
    counts: dict[str, int] = {}
    for (raw,) in rows:
        for t in (raw or '').split(','):
            t = t.strip()
            if not t:
                continue
            counts[t] = counts.get(t, 0) + 1
    out = sorted(({'tag': k, 'count': v} for k, v in counts.items()),
                 key=lambda r: (-r['count'], r['tag']))
    return jsonify({'status': 'success', 'tags': out})


# Regenerate the thumbnail for a single media item.
@media_bp.route('/api/media/<int:media_id>/regenerate-thumbnail', methods=['POST'])
@utils.api_auth_required(['media:write'])
@require_permission('media.upload')
def api_regenerate_thumbnail(media_id):
    media = Media.query.get_or_404(media_id)
    if media.media_type == 'webpage':
        return jsonify({'status': 'error',
                        'message': 'Use /refresh-screenshot for webpages.'}), 400

    src_abs = storage.absolute_path(media.file_path) if media.file_path else None
    if not src_abs or not os.path.isfile(src_abs):
        media.thumbnail_status = 'failed'
        db.session.commit()
        return jsonify({'status': 'error',
                        'message': 'Source file is missing on disk.'}), 410

    # Drop any prior thumbnail bytes before reserving a new path.
    if media.thumbnail_path:
        storage.delete(media.thumbnail_path)
    thumb_abs, thumb_rel = storage.reserve_path('thumbnail', '.png')
    try:
        if media.media_type == 'image':
            create_image_thumbnail(src_abs, thumb_abs)
        else:
            create_video_thumbnail(src_abs, thumb_abs)
        media.thumbnail_path = thumb_rel
        media.thumbnail_status = 'ok'
        media.thumbnail_generated_at = datetime.utcnow()
        db.session.commit()
        audit('media.thumbnail.regen', target_type='media',
              target_id=str(media_id), payload={'name': media.name})
        return jsonify({'status': 'success', 'media': media.to_dict()})
    except Exception as e:
        logger.error(f'regenerate thumbnail failed for {media_id}: {e}')
        media.thumbnail_status = 'failed'
        db.session.commit()
        return jsonify({'status': 'error', 'message': str(e)}), 500


# Orphan scan: report media rows whose file_path is missing on disk and
# (optionally) on-disk files in the tenant's uploads tree that no Media
# row points at. Read-only by default; pass ?cleanup=1 to remove the
# orphaned files (Media rows are never auto-deleted).
@media_bp.route('/api/media/orphans', methods=['GET'])
@login_required
@require_permission('media.read')
def api_media_orphans():
    cleanup = (request.args.get('cleanup', '').lower()
               in ('1', 'true', 'yes'))

    missing_rows = []
    for m in Media.query.all():
        if m.media_type == 'webpage' or not m.file_path:
            continue
        abs_p = storage.absolute_path(m.file_path)
        if not abs_p or not os.path.isfile(abs_p):
            missing_rows.append({'id': m.id, 'name': m.name,
                                 'file_path': m.file_path})

    # Build set of referenced rel_paths for this tenant.
    referenced = set()
    for m in Media.query.all():
        if m.file_path:
            referenced.add(m.file_path.replace('\\', '/'))
        if m.thumbnail_path:
            referenced.add(m.thumbnail_path.replace('\\', '/'))

    orphan_files = []
    domain_id = current_domain_id()
    tenant_root = None
    if domain_id is not None:
        try:
            tenant_root = storage._tenant_root(domain_id)
        except Exception:
            tenant_root = None
    if tenant_root and os.path.isdir(tenant_root):
        for dirpath, _dirs, files in os.walk(tenant_root):
            for fname in files:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(
                    full,
                    current_app.config['UPLOAD_FOLDER']
                ).replace('\\', '/')
                if rel in referenced:
                    continue
                try:
                    sz = os.path.getsize(full)
                except OSError:
                    sz = None
                orphan_files.append({'rel_path': rel, 'size': sz})

    removed = 0
    if cleanup and orphan_files:
        for o in orphan_files:
            if storage.delete(o['rel_path']):
                removed += 1
        audit('media.orphans.cleanup', target_type='media',
              payload={'removed': removed,
                       'candidates': len(orphan_files)})

    return jsonify({
        'status': 'success',
        'missing_rows': missing_rows,
        'orphan_files': orphan_files,
        'cleanup_removed': removed if cleanup else None,
    })


@media_bp.route('/media')
@login_required
@require_permission('media.read')
def media():
    media_items = Media.query.all()
    return render_template('media.html', media_items=media_items)
   
