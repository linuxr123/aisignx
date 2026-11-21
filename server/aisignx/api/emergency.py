"""Emergency messages over the API, so a panic button, a script or a webhook can raise one.

Needs a token with the emergency:send scope (editor or admin only). Sending works from
saved templates only: the message text, level and screens are decided in advance by a
person, so a leaked token can repeat a prepared message but cannot write a new one.
"""
from datetime import datetime, timedelta

from flask import g, jsonify, request

from ..audit import service as audit
from ..extensions import db
from ..models import EmergencyMessage, EmergencyPlan, EmergencyTemplate
from ..models.emergency_plan import REPEATS
from ..player.api import api_error
from ..timeutil import as_utc, iso_z, org_tz, utcnow
from ..tokens.auth import token_required
from . import bp


def _message_json(m: EmergencyMessage) -> dict:
    return {"id": m.id, "title": m.title, "level": m.level, "target": m.target_label, "live": m.live,
            "created_at": iso_z(m.created_at), "cleared_at": iso_z(m.cleared_at) if m.cleared_at else None}


@bp.get("/v1/emergency")
@token_required("emergency:send")
def emergency_live():
    from ..emergency import service  # imported late: avoids a circular import at start-up
    return jsonify(live=[_message_json(m) for m in service.live()])


@bp.get("/v1/emergency/templates")
@token_required("emergency:send")
def emergency_templates():
    rows = EmergencyTemplate.query.order_by(EmergencyTemplate.name).all()
    return jsonify(templates=[{"id": t.id, "name": t.name, "title": t.title, "level": t.level,
                               "target": t.target} for t in rows])


@bp.post("/v1/emergency/templates/<int:template_id>/send")
@token_required("emergency:send")
def emergency_send(template_id: int):
    from ..emergency import service
    tpl = db.session.get(EmergencyTemplate, template_id)
    if tpl is None:
        return api_error(404, "not_found", f"Template {template_id} not found.")
    # One live message per template: sending twice (a button pressed twice, a webhook retried)
    # must not stack duplicates.
    for m in service.live():
        if (m.title, m.message, m.level, m.target_label) == (tpl.title, tpl.message, tpl.level,
                                                             _target_label(tpl)):
            return jsonify(message=_message_json(m), already_live=True)
    try:
        msg = service.send_template(tpl, g.api_user)
    except service.TemplateError as exc:
        return api_error(409, "template_unusable", str(exc))
    audit.record("emergency.send", ("emergency", msg.id, msg.title),
                 detail={"template": tpl.name, "target": msg.target_label, "level": msg.level, "via": "api"})
    db.session.commit()
    screens = service.push(msg)
    return jsonify(message=_message_json(msg), screens=screens, already_live=False), 201


def _target_label(tpl: EmergencyTemplate) -> str:
    probe = EmergencyMessage(title=tpl.title)
    from ..targets import label, parse
    try:
        probe.scope, probe.display_id, probe.group_id = parse(tpl.target)
    except Exception:
        return ""
    return label(probe)[:120]


@bp.post("/v1/emergency/clear")
@token_required("emergency:send")
def emergency_clear():
    """Clear one live message ({"id": 12}) or every live message (no body)."""
    from ..emergency import service
    body = request.get_json(silent=True) or {}
    wanted = body.get("id", request.args.get("id", type=int))
    if wanted is not None:
        msg = db.session.get(EmergencyMessage, wanted) if isinstance(wanted, int) else None
        if msg is None:
            return api_error(404, "not_found", "No such emergency message.")
        cleared = [msg] if service.clear(msg, g.api_user) else []
    else:
        cleared = service.clear_all(g.api_user)
    if cleared:
        audit.record("emergency.clear_all" if wanted is None else "emergency.clear",
                     None if wanted is None else ("emergency", cleared[0].id, cleared[0].title),
                     detail={"cleared": [{"id": m.id, "title": m.title} for m in cleared], "via": "api"})
        db.session.commit()
        for m in cleared:
            service.push(m)
    return jsonify(cleared=[_message_json(m) for m in cleared])


# ---- Planning: the same prepared messages, at a chosen time (optionally repeating) -------------------------------------------
MAX_PENDING_PLANS = 200
MAX_DAYS_AHEAD = 400


def _plan_json(p: EmergencyPlan) -> dict:
    return {"id": p.id, "template_id": p.template_id, "template": p.template.name if p.template else None, "starts_at": iso_z(p.starts_at),
            "duration_min": p.duration_min, "repeat": p.repeat, "state": p.state, "note": p.note or None}


def _parse_start(value):
    """An ISO 8601 time: with an offset (or Z) it is exact; without one it is the organization's local time. None when unreadable."""
    if not isinstance(value, str) or not 10 <= len(value.strip()) <= 40:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return as_utc(parsed if parsed.tzinfo else parsed.replace(tzinfo=org_tz()))


@bp.get("/v1/emergency/plans")
@token_required("emergency:send")
def plans_list():
    """The pending plans (soonest first) and the ten most recent finished ones."""
    pending = EmergencyPlan.query.filter_by(state="pending").order_by(EmergencyPlan.starts_at).all()
    recent = EmergencyPlan.query.filter(EmergencyPlan.state != "pending").order_by(EmergencyPlan.starts_at.desc()).limit(10).all()
    return jsonify(pending=[_plan_json(p) for p in pending], recent=[_plan_json(p) for p in recent])


@bp.post("/v1/emergency/plans")
@token_required("emergency:send")
def plans_create():
    """Plan a saved message: {"template_id": 3, "starts_at": "2026-12-01T10:00:00-05:00", "duration_min": 30, "repeat": "weekdays"}.
    duration_min and repeat are optional. A request that repeats an existing pending plan returns that plan (a retried webhook is harmless)."""
    from ..emergency import plans as plan_service

    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return api_error(400, "bad_request", "Send a JSON object.")
    tpl = db.session.get(EmergencyTemplate, body.get("template_id")) if isinstance(body.get("template_id"), int) else None
    if tpl is None:
        return api_error(404, "not_found", "No such saved message (template_id).")
    starts = _parse_start(body.get("starts_at"))
    if starts is None:
        return api_error(400, "bad_start", 'starts_at must be an ISO 8601 time such as "2026-12-01T10:00:00-05:00" or "2026-12-01T10:00" (organization time).')
    now = utcnow()
    if starts <= now - timedelta(minutes=1):
        return api_error(400, "in_the_past", "starts_at is in the past.")
    if starts > now + timedelta(days=MAX_DAYS_AHEAD):
        return api_error(400, "too_far", f"starts_at is more than {MAX_DAYS_AHEAD} days ahead.")
    duration = body.get("duration_min")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= plan_service.MAX_DURATION_MIN):
        return api_error(400, "bad_duration", f"duration_min must be 1 to {plan_service.MAX_DURATION_MIN}, or left out (stays up until cleared).")
    repeat = body.get("repeat", "none")
    if repeat not in REPEATS:
        return api_error(400, "bad_repeat", "repeat must be one of: " + ", ".join(REPEATS) + ".")
    same = EmergencyPlan.query.filter_by(template_id=tpl.id, state="pending", repeat=repeat).filter(EmergencyPlan.starts_at == starts).first()
    if same is not None:
        return jsonify(plan=_plan_json(same), already_planned=True)
    if EmergencyPlan.query.filter_by(state="pending").count() >= MAX_PENDING_PLANS:
        return api_error(409, "too_many_plans", f"There are already {MAX_PENDING_PLANS} pending plans.")
    plan = EmergencyPlan(template_id=tpl.id, starts_at=starts, duration_min=duration, repeat=repeat, created_by_id=g.api_user.id)
    db.session.add(plan)
    db.session.flush()
    audit.record("emergency.plan_add", ("emergency_plan", plan.id, tpl.name),
                 detail={"starts_at": starts.isoformat(), "duration_min": duration, "repeat": repeat, "via": "api"})
    db.session.commit()
    return jsonify(plan=_plan_json(plan), already_planned=False), 201


@bp.delete("/v1/emergency/plans/<int:plan_id>")
@token_required("emergency:send")
def plans_cancel(plan_id: int):
    """Cancel a pending plan (a repeating one stops repeating). A plan that already ran stays as history."""
    plan = db.session.get(EmergencyPlan, plan_id)
    if plan is None:
        return api_error(404, "not_found", "No such plan.")
    if plan.state != "pending":
        return api_error(409, "not_pending", f"This plan is {plan.state}.")
    plan.state = "cancelled"
    audit.record("emergency.plan_cancel", ("emergency_plan", plan.id, plan.template.name if plan.template else ""), detail={"via": "api"})
    db.session.commit()
    return jsonify(plan=_plan_json(plan))
