"""JSON media API, authenticated with Bearer API tokens (see docs/api.md).

Each view answers on /api/v1/... and, where the PowerPoint add-in calls it, on the legacy
/api/media... path too. The legacy paths must never change. Errors are
{"error": code, "message": text}.
"""
from flask import g, jsonify, request
from werkzeug.exceptions import HTTPException

from ..audit import service as audit
from ..extensions import db
from ..media import service, storage
from ..media.probe import InvalidMediaError
from ..models import Media
from ..models.media import MEDIA_WEB
from ..player.api import api_error, http_error_response
from ..timeutil import iso_z
from ..tokens.auth import token_required
from . import bp

MAX_DURATION = 86400
MAX_PER_PAGE = 100
_ABSENT = object()


@bp.after_request
def _after(resp):
    resp.headers.setdefault("Cache-Control", "no-store")
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.errorhandler(HTTPException)
def _http_error(e):
    return http_error_response(e)


@bp.errorhandler(500)
def _server_error(_e):
    return api_error(500, "server_error", "Internal server error.")


def media_json(media: Media) -> dict:
    return {
        "id": media.id,
        "name": media.name,
        "type": media.media_type,
        "mime": media.mime_type,
        "size": media.file_size,
        "width": media.width,
        "height": media.height,
        "duration": media.effective_duration,
        "media_duration_s": media.duration_seconds,
        "default_duration": media.default_duration,
        "checksum_sha256": media.checksum_sha256,
        "url": media.url,
        "created_at": iso_z(media.created_at),
    }


def _not_found(media_id: int):
    return api_error(404, "not_found", f"Media {media_id} not found.")


def _int_arg(name: str, default: int, lo: int, hi: int) -> int:
    value = request.args.get(name, type=int)
    return default if value is None else max(lo, min(hi, value))


def _duration_field():
    """The "duration" form field: _ABSENT, an int 1..86400, or None when invalid.
    A blank value counts as absent."""
    raw = request.form.get("duration")
    if raw is None or not raw.strip():
        return _ABSENT
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    return value if 1 <= value <= MAX_DURATION else None


def _name_field():
    """The "name" form field stripped and cut to 200 characters, or None when blank."""
    name = (request.form.get("name") or "").strip()
    return name[:200] or None


def _upload_fields():
    """(file, name, duration, error_response) for an upload or replace request."""
    file_storage = request.files.get("file")
    if file_storage is None or not file_storage.filename:
        return None, None, None, api_error(400, "missing_file",
                                           'Send the file as the multipart field "file".')
    if storage.media_type_for(file_storage.filename) is None:
        return None, None, None, api_error(415, "unsupported_type",
                                           "Only image and video files can be uploaded.")
    duration = _duration_field()
    if duration is None:
        return None, None, None, api_error(
            400, "invalid_duration", f"duration must be a whole number from 1 to {MAX_DURATION}.")
    return file_storage, _name_field(), duration, None


@bp.get("/v1/media")
@bp.get("/media")
@token_required("media:read")
def media_list():
    page = _int_arg("page", 1, 1, 10**6)
    per_page = _int_arg("per_page", 50, 1, MAX_PER_PAGE)
    query = Media.query.order_by(Media.created_at.desc(), Media.id.desc())
    total = query.count()
    items = query.offset((page - 1) * per_page).limit(per_page).all()
    return jsonify(items=[media_json(m) for m in items], page=page, per_page=per_page,
                   total=total)


@bp.get("/v1/media/<int:media_id>")
@token_required("media:read")
def media_get(media_id: int):
    media = db.session.get(Media, media_id)
    if media is None:
        return _not_found(media_id)
    return jsonify(media_json(media))


@bp.post("/v1/media")
@bp.post("/media")
@token_required("media:write")
def media_upload():
    file_storage, name, duration, error = _upload_fields()
    if error is not None:
        return error
    try:
        media = service.create_from_upload(file_storage, g.api_user)
    except InvalidMediaError as exc:
        db.session.rollback()
        return api_error(400, "invalid_media", str(exc))
    if name:
        media.name = name
    if duration is not _ABSENT:
        media.default_duration = duration
    audit.record("media.upload", media)
    db.session.commit()
    resp = jsonify(media_json(media))
    resp.status_code = 201
    resp.headers["Location"] = f"/api/v1/media/{media.id}"
    return resp


@bp.post("/v1/media/<int:media_id>/replace")
@bp.post("/media/<int:media_id>/replace")
@token_required("media:write")
def media_replace(media_id: int):
    media = db.session.get(Media, media_id)
    if media is None:
        return _not_found(media_id)
    if media.media_type == MEDIA_WEB:
        return api_error(409, "not_replaceable",
                         f"Media {media_id} is a web page; only uploaded files can be replaced.")
    file_storage, name, duration, error = _upload_fields()
    if error is not None:
        return error
    old_checksum = media.checksum_sha256
    try:
        old = service.replace_file(media, file_storage)
    except InvalidMediaError as exc:
        db.session.rollback()
        return api_error(400, "invalid_media", str(exc))
    if name:
        media.name = name
    if duration is not _ABSENT:
        media.default_duration = duration
    audit.record("media.replace", media, detail={"old_checksum": old_checksum,
                                                 "filename": media.original_filename})
    service.commit_replace(media, old)
    return jsonify(media_json(media))
