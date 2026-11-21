"""App updates page: see which screens are behind, push to the ones you tick, watch each one (admin only)."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..extensions import db
from ..models import Display
from . import bp, updates
from .forms import ActionForm
from .routes import _publish


@bp.route("/updates")
@admin_required
def updates_page():
    displays = Display.query.order_by(Display.name).all()
    rows = updates.rows(displays)
    preselected = {i for i in request.args.getlist("id", type=int)}          # coming from ticked boxes on the Displays list
    return render_template("displays/updates.html", rows=rows, action_form=ActionForm(), preselected=preselected,
                           outdated=sum(1 for r in rows if r["status"] in ("outdated", "failed", "expired")))


@bp.route("/updates/push", methods=["POST"])
@admin_required
def updates_push():
    if not ActionForm().validate_on_submit():
        abort(400)
    ids = [i for i in request.form.getlist("display_id", type=int) if i]
    if not ids:
        flash("Tick at least one screen.", "warning")
        return redirect(url_for("displays.updates_page"))
    displays = Display.query.filter(Display.id.in_(ids)).order_by(Display.name).all()
    result = updates.push(displays, current_user)
    for d, cmd in result["commands"]:
        audit.record("display.agent_command", d, detail={"command": "update_app", "request_id": cmd.request_id, "via": "bulk update"})
    db.session.commit()
    woken = sum(1 for d, _c in result["commands"] if _publish(d, {"action": "commands"}, force=True))
    if result["sent"]:
        flash(f"Update sent to {len(result['sent'])} screen{'s' if len(result['sent']) != 1 else ''} ({woken} online now, the others when they check in): "
              + ", ".join(result["sent"][:8]) + (" ..." if len(result["sent"]) > 8 else ""), "success")
    for name, reason in result["skipped"]:
        flash(f"{name}: {reason}", "warning")
    return redirect(url_for("displays.updates_page"))
