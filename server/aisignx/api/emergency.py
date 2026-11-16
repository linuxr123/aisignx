"""Emergency messages over the API, so a panic button, a script or a webhook can raise one.

Needs a token with the emergency:send scope (editor or admin only). Sending works from
saved templates only: the message text, level and screens are decided in advance by a
person, so a leaked token can repeat a prepared message but cannot write a new one.
"""
from flask import g, jsonify, request

from ..audit import service as audit
from ..extensions import db
from ..models import EmergencyMessage, EmergencyTemplate
from ..player.api import api_error
from ..timeutil import iso_z
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
