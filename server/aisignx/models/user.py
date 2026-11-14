"""User accounts and roles.

Sessions and "remember me" cookies hold "<user id>:<session epoch>". The epoch is a Setting
that a restore replaces (backup/restore.py), so every session issued before the restore
stops working: a bare id could otherwise name a different user in the restored table.
"""
import hmac
import secrets
from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db, login_manager
from .setting import Setting

ROLE_ADMIN = "admin"
ROLE_EDITOR = "editor"
ROLE_VIEWER = "viewer"
ROLES = (ROLE_ADMIN, ROLE_EDITOR, ROLE_VIEWER)

# Higher rank includes the permissions of lower ranks.
_RANK = {ROLE_VIEWER: 0, ROLE_EDITOR: 1, ROLE_ADMIN: 2}

SESSION_EPOCH_KEY = "auth.session_epoch"


def session_epoch() -> str:
    return Setting.get(SESSION_EPOCH_KEY) or "0"


def new_session_epoch() -> None:
    """Sign everyone out: sessions issued before this stop loading. No commit."""
    Setting.set(SESSION_EPOCH_KEY, secrets.token_hex(16))


def _utcnow():
    return datetime.now(timezone.utc)


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    email = db.Column(db.String(255), nullable=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default=ROLE_VIEWER)
    is_active_flag = db.Column("is_active", db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    last_login_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def get_id(self) -> str:  # stored in the session and the remember cookie
        return f"{self.id}:{session_epoch()}"

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)

    @property
    def is_active(self) -> bool:  # used by Flask-Login
        return bool(self.is_active_flag)

    def has_role(self, minimum: str) -> bool:
        return _RANK.get(self.role, -1) >= _RANK[minimum]

    @property
    def is_admin(self) -> bool:
        return self.role == ROLE_ADMIN

    @property
    def can_edit(self) -> bool:
        return self.has_role(ROLE_EDITOR)

    def __repr__(self) -> str:
        return f"<User {self.username} ({self.role})>"


@login_manager.user_loader
def load_user(user_id: str):
    ident, sep, epoch = str(user_id or "").partition(":")
    if not sep or not hmac.compare_digest(epoch.encode(), session_epoch().encode()):
        return None  # issued before the last restore (or by an older version)
    try:
        return db.session.get(User, int(ident))
    except (TypeError, ValueError):
        return None
