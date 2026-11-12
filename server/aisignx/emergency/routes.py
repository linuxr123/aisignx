"""Emergency page: live messages, the send form (editors) and recent history.

Every write commits first, then service.push() sends the forced SSE events. The commit
hook separately hints "manifest" to every screen, so polling screens converge too.
"""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.orm import selectinload

from .. import targets
from ..auth.decorators import editor_required
from ..extensions import db
from ..models import EmergencyMessage
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
    return render_template("emergency/index.html", live=service.live(), history=history,
                           form=form, action_form=ActionForm()), status


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
    db.session.commit()
    for msg in cleared:
        service.push(msg)
    current_app.logger.warning("all emergencies (%d) cleared by user %s", len(cleared),
                               current_user.id)
    flash(f"Cleared {len(cleared)} emergency message{'' if len(cleared) == 1 else 's'}.",
          "success")
    return redirect(url_for("emergency.index"))
