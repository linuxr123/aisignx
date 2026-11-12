"""Player API (/api/player/v1): JSON only, used by the browser player and native clients.

Every error is {"error": "<code>", "message": "<short text>"}. Clients drop their
credential only on a 401 whose JSON error is "unauthorized"; 5xx, 503, 429 and network
errors mean keep playing and back off. See docs/player-api.md.
"""
import re
import time

from flask import Response, current_app, g, jsonify, request, send_from_directory
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from werkzeug.exceptions import HTTPException, NotFound

from ..displays import pairing
from ..extensions import db
from ..media import storage
from ..models import Display, Media
from ..models.media import MEDIA_IMAGE, MEDIA_VIDEO
from ..setup.service import setup_required
from ..timeutil import utcnow
from . import api_bp
from .auth import display_required, set_display_cookie
from .manifest import build_manifest

MAX_BODY = 8192
POLL_AFTER_S = 3
_CLIENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_SCREEN = 16384
_MAX_ID = 2**31 - 1

# HTTP status -> error code for exceptions that escape a view (and for routing errors).
_HTTP_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
               405: "method_not_allowed", 413: "too_large", 429: "too_many_pending",
               503: "busy"}


def api_error(status: int, code: str, message: str, retry_after: int | None = None):
    resp = jsonify(error=code, message=message)
    resp.status_code = status
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    if retry_after is not None:
        resp.headers["Retry-After"] = str(retry_after)
    return resp


def http_error_response(e: HTTPException):
    """JSON version of a werkzeug HTTP error (also used by the app-level handlers)."""
    status = e.code or 500
    code = _HTTP_CODES.get(status, "server_error" if status >= 500 else "bad_request")
    resp = api_error(status, code, e.name or "Error")
    if status == 405 and getattr(e, "valid_methods", None):
        resp.headers["Allow"] = ", ".join(e.valid_methods)
    return resp


@api_bp.before_request
def _before():
    request.max_content_length = MAX_BODY
    if setup_required():
        return api_error(503, "setup_required",
                         "AiSignX is not set up yet. Finish the setup wizard in the admin.",
                         retry_after=30)
    return None


@api_bp.after_request
def _after(resp):
    if "Cache-Control" not in resp.headers:
        resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@api_bp.errorhandler(HTTPException)
def _http_error(e):
    return http_error_response(e)


@api_bp.errorhandler(500)
def _server_error(_e):
    return api_error(500, "server_error", "Internal server error.")


# --- request body helpers ---------------------------------------------------------------

_INVALID = object()


def _body():
    """The JSON object body; {} when there is no body; None when it is not an object."""
    if not request.get_data(cache=True):
        return {}
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else None


def _text(value, limit: int):
    return value[:limit] if isinstance(value, str) else None


def _client_id(value):
    if value is None:
        return None
    if isinstance(value, str) and _CLIENT_ID.match(value):
        return value
    return _INVALID


def _int(value, maximum: int):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return max(0, min(maximum, int(value)))


def _bad(message: str):
    return api_error(400, "bad_request", message)


def _heartbeat_s() -> int:
    return int(current_app.config["PLAYER_HEARTBEAT_SECONDS"])


# --- pairing ----------------------------------------------------------------------------

@api_bp.post("/pair")
def pair():
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    client_id = _client_id(body.get("client_id"))
    if client_id is _INVALID:
        return _bad("Invalid client_id.")
    try:
        row, secret = pairing.start(request.remote_addr, client_id,
                                    _text(body.get("device"), 120))
        code = row.code
        db.session.commit()
    except pairing.TooManyPending:
        db.session.rollback()
        return api_error(429, "too_many_pending",
                         "Too many screens are waiting to be paired. Try again soon.",
                         retry_after=60)
    except IntegrityError:
        db.session.rollback()
        return api_error(503, "busy", "Please try again.", retry_after=5)
    return jsonify(
        code=code,
        display_code=pairing.display_code(code),
        pairing_secret=secret,
        expires_in=int(current_app.config["PAIRING_CODE_TTL_SECONDS"]),
        poll_after_s=POLL_AFTER_S,
    ), 201


@api_bp.post("/pair/status")
def pair_status():
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    secret = body.get("pairing_secret")
    credential = body.get("credential", "bearer")
    if not isinstance(secret, str) or not 1 <= len(secret) <= 128:
        return _bad("Missing pairing_secret.")
    if credential not in ("bearer", "cookie"):
        return _bad("credential must be \"bearer\" or \"cookie\".")

    status, value = pairing.claim(secret)
    if status == pairing.PAIRED:
        display, raw = value
        info = {"id": display.id, "name": display.name}
    db.session.commit()

    if status == pairing.PENDING:
        return jsonify(status="pending", expires_in=value)
    if status == pairing.EXPIRED:
        return api_error(404, "expired", "This pairing code has expired. Request a new one.")
    payload = {"status": "paired", "display": info, "heartbeat_s": _heartbeat_s()}
    if credential == "bearer":
        payload["token"] = raw
    resp = jsonify(payload)
    if credential == "cookie":
        set_display_cookie(resp, raw)
    return resp


# --- paired displays --------------------------------------------------------------------

@api_bp.get("/manifest")
@display_required
def manifest():
    body = build_manifest(g.display)
    version = body["version"]
    if request.if_none_match.contains(version):
        resp = Response(status=304)
    else:
        resp = jsonify(body)
    resp.set_etag(version)
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@api_bp.post("/heartbeat")
@display_required
def heartbeat():
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    client_id = _client_id(body.get("client_id"))
    if client_id is _INVALID:
        return _bad("Invalid client_id.")

    version = build_manifest(g.display)["version"]
    # A Core UPDATE: no ORM flush, so heartbeats never trigger a manifest broadcast.
    db.session.execute(
        update(Display).where(Display.id == g.display.id).values(
            last_seen_at=utcnow(),
            last_ip=(request.remote_addr or "")[:45] or None,
            client_id=client_id,
            player_version=_text(body.get("player_version"), 32),
            reported_version=_text(body.get("manifest_version"), 16),
            current_item_id=_int(body.get("current_item_id"), _MAX_ID),
            screen_width=_int(body.get("screen_w"), _MAX_SCREEN),
            screen_height=_int(body.get("screen_h"), _MAX_SCREEN),
        ).execution_options(synchronize_session=False)
    )
    db.session.commit()
    return jsonify(server_time_ms=int(time.time() * 1000), manifest_version=version,
                   heartbeat_s=_heartbeat_s())


@api_bp.get("/media/<int:media_id>/<name>")
@display_required
def media_file(media_id: int, name: str):
    media = db.session.get(Media, media_id)
    if (media is None or not media.stored_name or media.stored_name != name
            or media.media_type not in (MEDIA_IMAGE, MEDIA_VIDEO)):
        return api_error(404, "not_found", "No such media.")
    try:
        resp = send_from_directory(storage.media_dir(), name, mimetype=media.mime_type,
                                   conditional=True)
    except NotFound:
        return api_error(404, "not_found", "The media file is missing.")
    # Stored names are random and never reused, so the content is immutable. Private:
    # shared caches must not keep media that needs a display credential.
    resp.cache_control.no_cache = None
    resp.cache_control.public = False
    resp.cache_control.private = True
    resp.cache_control.max_age = 31536000
    resp.cache_control.immutable = True
    return resp
