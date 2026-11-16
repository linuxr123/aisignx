"""Scheduled emergencies: a job sends a saved message at its start time and clears it at its end time.

A plan that is more than GRACE_MIN minutes late (the server was off at the time) is marked
"missed" and never sent: a fire drill announced an hour late is worse than none.
"""
import logging
from datetime import timedelta

from ..audit import service as audit
from ..extensions import db
from ..models import EmergencyMessage, EmergencyPlan
from ..timeutil import as_utc, utcnow
from . import service

log = logging.getLogger(__name__)
GRACE_MIN = 10
MAX_DURATION_MIN = 24 * 60
CLEAR_EXPIRED = "expired"


def fire_due(now=None) -> int:
    """Send every pending plan whose time has come. Returns how many were sent."""
    now = now or utcnow()
    sent = 0
    due = (EmergencyPlan.query.filter(EmergencyPlan.state == "pending", EmergencyPlan.starts_at <= now)
           .order_by(EmergencyPlan.starts_at).all())
    for plan in due:
        late = now - as_utc(plan.starts_at)
        if late > timedelta(minutes=GRACE_MIN):
            plan.state, plan.note = "missed", f"Not sent: the server was {int(late.total_seconds() // 60)} minutes late."
            db.session.commit()
            log.warning("emergency plan %s missed (%s late)", plan.id, late)
            continue
        try:
            msg = service.send_template(plan.template, plan.created_by)
        except service.TemplateError as exc:
            plan.state, plan.note = "failed", str(exc)[:200]
            db.session.commit()
            log.warning("emergency plan %s failed: %s", plan.id, exc)
            continue
        if plan.duration_min:
            msg.ends_at = as_utc(plan.starts_at) + timedelta(minutes=plan.duration_min)
        plan.state, plan.message_id = "fired", msg.id
        audit.record("emergency.send", ("emergency", msg.id, msg.title),
                     detail={"plan": plan.id, "template": plan.template.name, "target": msg.target_label,
                             "level": msg.level, "via": "schedule"})
        db.session.commit()
        service.push(msg)
        log.warning("emergency plan %s sent message %s (%s)", plan.id, msg.id, msg.title)
        sent += 1
    return sent


def expire_due(now=None) -> int:
    """Clear live messages whose end time has passed. Returns how many were cleared."""
    now = now or utcnow()
    done = (EmergencyMessage.query.filter(EmergencyMessage.cleared_at.is_(None),
                                          EmergencyMessage.ends_at.isnot(None),
                                          EmergencyMessage.ends_at <= now).all())
    cleared = [m for m in done if service.clear(m, None, CLEAR_EXPIRED)]
    if cleared:
        audit.record("emergency.clear_all", detail={"cleared": [{"id": m.id, "title": m.title} for m in cleared],
                                                    "via": "schedule"})
        db.session.commit()
        for m in cleared:
            service.push(m)
    return len(cleared)


def job() -> str | None:
    sent, cleared = fire_due(), expire_due()
    return f"sent {sent}, cleared {cleared}" if sent or cleared else None
