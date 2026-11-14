"""Create and revoke personal API tokens. Only a sha256 of a token is stored; the raw
value is shown to its owner once."""
import hashlib
import secrets
from datetime import timedelta

from ..audit import service as audit
from ..extensions import db
from ..models import SCOPE_MIN_ROLE, SCOPES, ApiToken
from ..timeutil import utcnow

TOKEN_PREFIX = "asx_"
EXPIRY_CHOICES = (None, 30, 90, 365)  # days; None = never


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def allowed_scopes(user) -> list[str]:
    """The scopes this user's role may hold, in SCOPES order."""
    return [s for s in SCOPES if user.has_role(SCOPE_MIN_ROLE[s])]


def create(user, name: str, scopes, expires_days: int | None) -> tuple[ApiToken, str]:
    """Add a token for user (no commit). Returns (token, raw). Raises ValueError for a
    blank name, empty or unknown scopes, or a scope the user's role may not hold."""
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("Give the token a name.")
    wanted = [s for s in SCOPES if s in set(scopes or ())]
    if not wanted or len(wanted) != len(set(scopes)):
        raise ValueError("Choose at least one valid scope.")
    allowed = allowed_scopes(user)
    denied = [s for s in wanted if s not in allowed]
    if denied:
        raise ValueError(f"Your role cannot hold {', '.join(denied)}.")
    if expires_days is not None and expires_days < 1:
        raise ValueError("Expiry must be at least one day.")

    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = ApiToken(
        user_id=user.id,
        name=name,
        prefix=raw[:12],
        token_hash=hash_token(raw),
        scopes=" ".join(wanted),
        created_at=utcnow(),
        expires_at=utcnow() + timedelta(days=expires_days) if expires_days else None,
    )
    db.session.add(token)
    audit.record("token.create", token,
                 detail={"name": name, "scopes": wanted, "prefix": token.prefix,
                         "expires_days": expires_days})
    return token, raw


def revoke(token: ApiToken) -> bool:
    """Revoke a token (no commit). Returns False when it was already revoked."""
    if token.revoked_at is not None:
        return False
    token.revoked_at = utcnow()
    audit.record("token.revoke", token,
                 detail={"prefix": token.prefix, "owner": token.user.username})
    return True


def status(token: ApiToken) -> str:
    if token.revoked_at is not None:
        return "revoked"
    if token.is_expired():
        return "expired"
    return "active"
