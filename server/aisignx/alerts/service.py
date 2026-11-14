"""Open, resolve, acknowledge and purge alerts.

At most one open alert exists per (kind, key) (partial unique index uq_alerts_open).
open_alert() and resolve() commit their own changes, so call them with nothing else
pending in the session.
"""
import logging
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Alert, Setting
from ..timeutil import utcnow

log = logging.getLogger(__name__)


def get_open(kind: str, key: str) -> Alert | None:
    return Alert.query.filter_by(kind=kind, key=key, resolved_at=None).first()


def open_keys(kind: str) -> set[str]:
    """Keys of the open alerts of one kind."""
    rows = db.session.execute(sa.select(Alert.key).where(
        Alert.kind == kind, Alert.resolved_at.is_(None))).scalars()
    return set(rows)


def _refresh(alert: Alert, severity: str, title: str, detail: str) -> None:
    alert.last_seen_at = utcnow()
    if alert.severity != severity:
        alert.severity = severity
    if alert.title != title:
        alert.title = title
    if (alert.detail or "") != (detail or ""):
        alert.detail = detail


def open_alert(kind: str, key: str, severity: str, title: str, detail: str = "") -> Alert:
    """Open the alert, or refresh the one already open (idempotent). Commits."""
    title, key = title[:200], key[:100]
    alert = get_open(kind, key)
    if alert is None:
        now = utcnow()
        alert = Alert(kind=kind, key=key, severity=severity, title=title, detail=detail,
                      opened_at=now, last_seen_at=now)
        db.session.add(alert)
        try:
            db.session.commit()
            log.warning("Alert opened: %s %s: %s", kind, key, title)
            return alert
        except IntegrityError:  # another writer opened it first
            db.session.rollback()
            alert = get_open(kind, key)
            if alert is None:
                raise
    _refresh(alert, severity, title, detail)
    db.session.commit()
    return alert


def resolve(kind: str, key: str) -> bool:
    """Resolve the open alert for (kind, key), if any. Commits. True if one was open."""
    alert = get_open(kind, key)
    if alert is None:
        return False
    alert.resolved_at = utcnow()
    db.session.commit()
    log.info("Alert resolved: %s %s", kind, key)
    return True


def ack(alert: Alert, user) -> bool:
    """Mark an open alert as acknowledged (no commit). False if it is resolved or
    already acknowledged."""
    if alert.resolved_at is not None or alert.acknowledged_at is not None:
        return False
    alert.acknowledged_at = utcnow()
    alert.acknowledged_by_id = getattr(user, "id", None)
    return True


def open_count() -> int:
    """Open alerts nobody has acknowledged yet (the nav badge)."""
    return Alert.query.filter(Alert.resolved_at.is_(None),
                              Alert.acknowledged_at.is_(None)).count()


def purge(days: int) -> int:
    """Delete resolved alerts resolved more than `days` days ago."""
    cutoff = utcnow() - timedelta(days=days)
    result = db.session.execute(sa.delete(Alert.__table__).where(
        Alert.__table__.c.resolved_at.is_not(None),
        Alert.__table__.c.resolved_at < cutoff))
    db.session.commit()
    return result.rowcount or 0


def purge_job() -> str:
    """Job: apply alerts.retention_days to resolved alerts (0 keeps everything)."""
    days = Setting.get_int("alerts.retention_days", lo=0, hi=3650)
    if not days:
        return "retention off"
    return f"deleted {purge(days)} resolved alerts older than {days} days"
