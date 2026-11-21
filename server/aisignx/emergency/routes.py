"""Emergency page: live messages, the send form (editors) and recent history.

Every write commits first, then service.push() sends the forced SSE events. The commit
hook separately hints "manifest" to every screen, so polling screens converge too.
"""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from .. import targets
from ..audit import service as audit
from ..auth.decorators import editor_required
from ..extensions import db
from ..models import EmergencyMessage, EmergencyPlan, EmergencyTemplate
from ..models.emergency_plan import REPEATS
from ..timeutil import as_utc, org_tz, utcnow
from . import bp, service
from .forms import ActionForm, EmergencyForm

HISTORY_LIMIT = 50


def _get_message_or_404(message_id: int) -> EmergencyMessage:
    msg = db.session.get(EmergencyMessage, message_id)
    if msg is None:
        abort(404)
    return msg


def _require_action_form() -> None:
    if not ActionForm().validate_on_submit():
        abort(400)


def _target(msg: EmergencyMessage) -> tuple:
    return ("emergency", msg.id, msg.title)


def _screens(n: int) -> str:
    return f"{n} screen{'' if n == 1 else 's'}"


def _prefill(form: EmergencyForm, source: EmergencyMessage) -> None:
    """"Send again": copy a previous message into the form."""
    form.title.data = source.title
    form.message.data = source.message
    form.level.data = source.level
    form.show_text.data = source.show_text
    form.playlist.data = source.playlist_id or 0
    # A message whose target was deleted must not silently widen to all screens.
    target_gone = source.scope != "all" and source.display_id is None and source.group_id is None
    if not target_gone:
        form.target.data = targets.encode(source)


def _render(form: EmergencyForm | None, status: int = 200):
    history = (EmergencyMessage.query
               .options(selectinload(EmergencyMessage.created_by),
                        selectinload(EmergencyMessage.cleared_by))
               .order_by(EmergencyMessage.created_at.desc(), EmergencyMessage.id.desc())
               .limit(HISTORY_LIMIT).all())
    templates = EmergencyTemplate.query.order_by(EmergencyTemplate.name).all()
    plans = (EmergencyPlan.query.order_by(EmergencyPlan.starts_at.desc()).limit(20).all())
    target_names = {str(c.value): c.label for c in targets.choices()}
    return render_template("emergency/index.html", live=service.live(), history=history,
                           templates=templates, plans=plans, form=form, target_names=target_names, action_form=ActionForm()), status


@bp.route("/")
@login_required
def index():
    form = None
    if current_user.can_edit:
        form = EmergencyForm(formdata=None)
        source_id = request.args.get("from", type=int)
        if source_id:
            source = db.session.get(EmergencyMessage, source_id)
            if source is not None:
                _prefill(form, source)
    return _render(form)


@bp.route("/send", methods=["POST"])
@editor_required
def send():
    form = EmergencyForm()
    if not form.validate_on_submit():
        flash("The message was not sent. Check the form below.", "danger")
        return _render(form, 400)
    msg = service.send(form, current_user)
    audit.record("emergency.send", _target(msg),
                 detail={"target": msg.target_label, "scope": msg.scope,
                         "display_id": msg.display_id, "group_id": msg.group_id,
                         "level": msg.level,
                         "playlist_id": msg.playlist_id, "show_text": msg.show_text})
    db.session.commit()
    sent = service.push(msg)
    current_app.logger.warning("emergency %s (%s, %s) sent to %s by user %s: %s",
                               msg.id, msg.level, msg.target_label, _screens(sent),
                               current_user.id, msg.title)
    flash(f"Emergency message sent to {_screens(sent)}.", "success" if sent else "warning")
    return redirect(url_for("emergency.index"))


@bp.route("/<int:message_id>/clear", methods=["POST"])
@editor_required
def clear(message_id: int):
    _require_action_form()
    msg = _get_message_or_404(message_id)
    if not service.clear(msg, current_user):
        flash(f"{msg.title} was already cleared.", "info")
        return redirect(url_for("emergency.index"))
    audit.record("emergency.clear", _target(msg),
                 detail={"reason": msg.clear_reason, "target": msg.target_label})
    db.session.commit()
    service.push(msg)
    current_app.logger.warning("emergency %s (%s) cleared by user %s", msg.id, msg.title,
                               current_user.id)
    flash(f"Cleared {msg.title}.", "success")
    return redirect(url_for("emergency.index"))


@bp.route("/clear-all", methods=["POST"])
@editor_required
def clear_all():
    _require_action_form()
    cleared = service.clear_all(current_user)
    if not cleared:
        flash("No emergency message is live.", "info")
        return redirect(url_for("emergency.index"))
    audit.record("emergency.clear_all",
                 detail={"cleared": [{"id": m.id, "title": m.title} for m in cleared]})
    db.session.commit()
    for msg in cleared:
        service.push(msg)
    current_app.logger.warning("all emergencies (%d) cleared by user %s", len(cleared),
                               current_user.id)
    flash(f"Cleared {len(cleared)} emergency message{'' if len(cleared) == 1 else 's'}.",
          "success")
    return redirect(url_for("emergency.index"))


@bp.route("/templates", methods=["POST"])
@editor_required
def save_template():
    """Save the send form as a template (nothing is sent)."""
    form = EmergencyForm()
    if not form.validate_on_submit():
        flash("The template was not saved. Check the form below.", "danger")
        return _render(form, 400)
    try:
        tpl = service.save_template(request.form.get("template_name", ""), form, current_user)
    except service.TemplateError as exc:
        flash(str(exc), "danger")
        return _render(form, 400)
    audit.record("emergency.template_save", ("emergency_template", tpl.id, tpl.name))
    db.session.commit()
    flash(f"Saved the template {tpl.name}.", "success")
    return redirect(url_for("emergency.index"))


@bp.route("/templates/<int:template_id>/send", methods=["POST"])
@editor_required
def send_template(template_id: int):
    _require_action_form()
    tpl = db.session.get(EmergencyTemplate, template_id) or abort(404)
    try:
        msg = service.send_template(tpl, current_user)
    except service.TemplateError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("emergency.index"))
    audit.record("emergency.send", _target(msg),
                 detail={"template": tpl.name, "target": msg.target_label, "level": msg.level})
    db.session.commit()
    sent = service.push(msg)
    current_app.logger.warning("emergency %s from template %r sent to %s by user %s", msg.id, tpl.name,
                               _screens(sent), current_user.id)
    flash(f"Sent {tpl.name} to {_screens(sent)}.", "success" if sent else "warning")
    return redirect(url_for("emergency.index"))


@bp.route("/templates/<int:template_id>/delete", methods=["POST"])
@editor_required
def delete_template(template_id: int):
    _require_action_form()
    tpl = db.session.get(EmergencyTemplate, template_id) or abort(404)
    audit.record("emergency.template_delete", ("emergency_template", tpl.id, tpl.name))
    db.session.delete(tpl)
    db.session.commit()
    flash("Template removed.", "info")
    return redirect(url_for("emergency.index"))


@bp.route("/plans", methods=["POST"])
@editor_required
def add_plan():
    """Schedule a saved message: starts_at is a local time in the organization's time zone."""
    from datetime import datetime, timedelta

    from . import plans as plan_service

    if not ActionForm().validate_on_submit():
        abort(400)
    tpl = db.session.get(EmergencyTemplate, request.form.get("template_id", type=int) or 0)
    try:
        local = datetime.strptime(request.form.get("starts_at", ""), "%Y-%m-%dT%H:%M")
    except ValueError:
        local = None
    duration = request.form.get("duration_min", type=int)
    if tpl is None or local is None:
        flash("Choose a saved message and a start time.", "danger")
        return redirect(url_for("emergency.index"))
    starts = as_utc(local.replace(tzinfo=org_tz()))
    if starts <= utcnow() - timedelta(minutes=1):
        flash("The start time is in the past.", "danger")
        return redirect(url_for("emergency.index"))
    if duration is not None and not 1 <= duration <= plan_service.MAX_DURATION_MIN:
        flash("Stay up for 1 to 1440 minutes, or leave it empty.", "danger")
        return redirect(url_for("emergency.index"))
    repeat = request.form.get("repeat", "none")
    if repeat not in REPEATS:
        repeat = "none"
    plan = EmergencyPlan(template_id=tpl.id, starts_at=starts, duration_min=duration, repeat=repeat,
                         created_by_id=current_user.id)
    db.session.add(plan)
    db.session.flush()
    audit.record("emergency.plan_add", ("emergency_plan", plan.id, tpl.name),
                 detail={"starts_at": starts.isoformat(), "duration_min": duration, "repeat": repeat})
    db.session.commit()
    flash(f"Scheduled {tpl.name}.", "success")
    return redirect(url_for("emergency.index"))


@bp.route("/plans/<int:plan_id>/cancel", methods=["POST"])
@editor_required
def cancel_plan(plan_id: int):
    _require_action_form()
    plan = db.session.get(EmergencyPlan, plan_id) or abort(404)
    if plan.state == "pending":
        plan.state = "cancelled"
        audit.record("emergency.plan_cancel", ("emergency_plan", plan.id, plan.template.name))
        db.session.commit()
        flash("Cancelled. A repeating message stops repeating." if plan.repeat != "none" else "Cancelled.", "info")
    return redirect(url_for("emergency.index"))
