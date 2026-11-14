"""Personal API tokens (Bearer) for the JSON API. Only a sha256 of the token is stored."""
from datetime import datetime

from ..extensions import db
from ..timeutil import as_utc, utcnow

SCOPES = ("media:read", "media:write")
# The lowest role that may hold (and use) each scope. Checked on every request.
SCOPE_MIN_ROLE = {"media:read": "viewer", "media:write": "editor"}


class ApiToken(db.Model):
    __tablename__ = "api_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    name = db.Column(db.String(80), nullable=False)
    prefix = db.Column(db.String(16), nullable=False)  # first characters, shown in lists
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    scopes = db.Column(db.String(64), nullable=False, default="")  # space-separated
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_used_ip = db.Column(db.String(45), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship("User")

    @property
    def scope_set(self) -> set[str]:
        return set((self.scopes or "").split())

    def is_expired(self, now: datetime | None = None) -> bool:
        return self.expires_at is not None and as_utc(self.expires_at) <= (now or utcnow())

    def is_usable(self, now: datetime | None = None) -> bool:
        """Not revoked and not expired (the owner's state is checked separately)."""
        return self.revoked_at is None and not self.is_expired(now)

    def __repr__(self) -> str:
        return f"<ApiToken {self.id} {self.prefix}... user={self.user_id}>"
