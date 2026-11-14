"""Bearer API token authentication for the JSON API (aisignx.api).

    @token_required("media:write")
    def view(): ...   # g.api_user and g.api_token are set

Only the Authorization header is read, never the session cookie or the URL. The token's
scope and the owner's *current* role are checked on every request, so demoting a user
takes effect at once.

Every 401 counts against the client IP in the login limiter's IP bucket
(auth.ratelimit), so guessing tokens is throttled like guessing passwords: once the IP
is over the limit, API calls get 429 with Retry-After until the window passes.
"""
from datetime import timedelta
from functools import wraps

from flask import g, request
from sqlalchemy import update

from ..auth import ratelimit
from ..extensions import db
from ..models import SCOPE_MIN_ROLE, ApiToken
from ..player.api import api_error
from ..timeutil import as_utc, utcnow
from .service import hash_token

MIN_LEN, MAX_LEN = 20, 200
LAST_USED_EVERY = timedelta(seconds=60)

_ROLE_MESSAGES = {"media:write": "Your role can no longer upload media.",
                  "media:read": "Your role can no longer read media."}


def bearer_token() -> str | None:
    """The raw token from "Authorization: Bearer <token>", or None."""
    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    value = value.strip()
    if scheme.lower() != "bearer" or not (MIN_LEN <= len(value) <= MAX_LEN):
        return None
    return value


def _unauthorized():
    ratelimit.api_failed(request.remote_addr)
    resp = api_error(401, "invalid_token", "API token missing, invalid, expired or revoked.")
    resp.headers["WWW-Authenticate"] = 'Bearer realm="aisignx", error="invalid_token"'
    return resp


def authenticate():
    """The usable ApiToken for this request, or None."""
    raw = bearer_token()
    if raw is None:
        return None
    token = ApiToken.query.filter_by(token_hash=hash_token(raw)).first()
    if token is None or not token.is_usable():
        return None
    user = token.user
    if user is None or not user.is_active:
        return None
    return token


def _touch(token: ApiToken) -> None:
    """Record last use, at most once a minute (a Core UPDATE, so nothing broadcasts)."""
    now = utcnow()
    last = as_utc(token.last_used_at)
    if last is not None and now - last < LAST_USED_EVERY:
        return
    db.session.execute(
        update(ApiToken).where(ApiToken.id == token.id)
        .values(last_used_at=now, last_used_ip=(request.remote_addr or "")[:45] or None)
        .execution_options(synchronize_session=False))
    db.session.commit()


def check(scope: str):
    """None when the request may go on (g.api_user/g.api_token set), else an error
    response."""
    wait = ratelimit.api_blocked(request.remote_addr)
    if wait is not None:
        return api_error(429, "rate_limited",
                         "Too many failed API sign-ins from this address. Try again later.",
                         retry_after=wait)
    token = authenticate()
    if token is None:
        return _unauthorized()
    if scope not in token.scope_set:  # an empty scope set never passes
        return api_error(403, "insufficient_scope", f"This token lacks {scope}.")
    user = token.user
    if not user.has_role(SCOPE_MIN_ROLE[scope]):
        message = _ROLE_MESSAGES.get(scope, "Your role no longer allows this.")
        return api_error(403, "forbidden", message)
    _touch(token)
    g.api_token = token
    g.api_user = token.user
    return None


def token_required(scope: str):
    if scope not in SCOPE_MIN_ROLE:
        raise ValueError(f"unknown scope {scope!r}")

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            error = check(scope)
            if error is not None:
                return error
            return view(*args, **kwargs)

        return wrapped

    return decorator
