"""Operator alerts, derived from facts by the alerts sweep (see alerts/rules.py).

At most one open alert exists per (kind, key): a partial unique index on the rows with
resolved_at IS NULL enforces it.
"""
from ..extensions import db
from ..timeutil import utcnow

KINDS = ("display_offline", "disk_low", "login_burst", "backup_failed", "backup_stale",
         "job_failed")
SEVERITIES = ("warning", "critical")

_OPEN = db.text("resolved_at IS NULL")


class Alert(db.Model):
    __tablename__ = "alerts"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(32), nullable=False)
    key = db.Column(db.String(100), nullable=False)  # e.g. "display:12", "disk:uploads"
    severity = db.Column(db.String(16), nullable=False, default="warning")
    title = db.Column(db.String(200), nullable=False)
    detail = db.Column(db.Text, nullable=True)
    opened_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    resolved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    acknowledged_at = db.Column(db.DateTime(timezone=True), nullable=True)
    acknowledged_by_id = db.Column(db.Integer,
                                   db.ForeignKey("users.id", ondelete="SET NULL"),
                                   nullable=True)
    notified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    resolve_notified_at = db.Column(db.DateTime(timezone=True), nullable=True)
    notify_attempts = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    last_notify_error = db.Column(db.String(255), nullable=True)

    acknowledged_by = db.relationship("User")

    __table_args__ = (
        db.Index("uq_alerts_open", "kind", "key", unique=True,
                 sqlite_where=_OPEN, postgresql_where=_OPEN),
    )

    @property
    def is_open(self) -> bool:
        return self.resolved_at is None

    def __repr__(self) -> str:
        state = "open" if self.is_open else "resolved"
        return f"<Alert {self.id} {self.kind} {self.key} {state}>"
