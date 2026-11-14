"""Audit log: who did what, when and from where. Rows are written by audit/service.py."""
import json

from ..extensions import db
from ..timeutil import utcnow

VIA = ("web", "api", "cli", "system")


class AuditLog(db.Model):
    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), nullable=False, index=True, default=utcnow)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"),
                        nullable=True)
    username = db.Column(db.String(64), nullable=True)  # kept after the user is deleted
    via = db.Column(db.String(8), nullable=True)  # web | api | cli | system
    token_id = db.Column(db.Integer, nullable=True)  # no FK: tokens get deleted
    action = db.Column(db.String(64), nullable=False)
    target_type = db.Column(db.String(32), nullable=True)
    target_id = db.Column(db.Integer, nullable=True)
    target_name = db.Column(db.String(200), nullable=True)
    ip = db.Column(db.String(45), nullable=True)
    detail = db.Column(db.Text, nullable=True)  # JSON, redacted, at most 4 KB

    __table_args__ = (
        db.Index("ix_audit_log_action_ts", "action", "ts"),
        db.Index("ix_audit_log_user_id_ts", "user_id", "ts"),
        db.Index("ix_audit_log_target", "target_type", "target_id"),
    )

    @property
    def detail_data(self):
        """The detail as Python data (None if empty or unreadable)."""
        if not self.detail:
            return None
        try:
            return json.loads(self.detail)
        except ValueError:
            return None

    def __repr__(self) -> str:
        return f"<AuditLog {self.id} {self.action} by {self.username}>"
