"""Display credentials: 256-bit random tokens, stored only as sha256.

Browsers carry the token in an HttpOnly cookie; native clients send
`Authorization: Bearer <token>`. Tokens are never read from the query string.
"""
import hashlib
import secrets
from functools import wraps

from flask import current_app, g, jsonify, request

from ..extensions import db
from ..models import Display

COOKIE_NAME = "aisignx_display"
COOKIE_MAX_AGE = 315360000  # ten years; revocation happens server-side
_MAX_TOKEN_LENGTH = 128


def new_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _cookie_path() -> str:
    return (request.script_root or "") + "/api/player"


def set_display_cookie(resp, raw: str) -> None:
    resp.set_cookie(
        COOKIE_NAME, raw, max_age=COOKIE_MAX_AGE, path=_cookie_path(),
        secure=bool(current_app.config.get("SESSION_COOKIE_SECURE")),
        httponly=True, samesite="Strict",
    )


def clear_display_cookie(resp) -> None:
    resp.delete_cookie(COOKIE_NAME, path=_cookie_path(), httponly=True, samesite="Strict")


def _token_from_request() -> str | None:
    header = request.headers.get("Authorization", "")
    if header[:7].lower() == "bearer ":
        token = header[7:].strip()
        if token:
            return token
    return request.cookies.get(COOKIE_NAME) or None


def unauthorized_response():
    """JSON 401 that also clears the browser cookie. Clients re-pair only on this."""
    resp = jsonify(error="unauthorized", message="This display is not paired.")
    resp.status_code = 401
    resp.headers["Cache-Control"] = "no-store"
    clear_display_cookie(resp)
    return resp


def current_display() -> Display | None:
    token = _token_from_request()
    if not token or len(token) > _MAX_TOKEN_LENGTH:
        return None
    # Equality on the hash of a 256-bit secret is not timing-sensitive.
    return db.session.query(Display).filter_by(token_hash=hash_token(token)).first()


def display_required(view):
    """Require a paired display and set g.display; otherwise return the JSON 401."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        display = current_display()
        if display is None:
            return unauthorized_response()
        g.display = display
        return view(*args, **kwargs)

    return wrapped
