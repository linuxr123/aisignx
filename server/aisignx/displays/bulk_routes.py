"""Edit several screens at once (editors for the general settings, admins for the kiosk and agent switches)."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..audit import service as audit
from ..auth.decorators import editor_required
from ..extensions import db
from ..models import Display
from . import bp, bulk


def _ids() -> list[int]:
    raw = request.values.getlist("id") or request.values.get("ids", "").split(",")
    out = []
    for item in raw:
        item = str(item).strip()
        if item.isdigit() and int(item) not in out:
            out.append(int(item))
    return out[:200]


@bp.route("/bulk", methods=["GET", "POST"])
@editor_required
def bulk_edit():
    ids = _ids()
    displays = Display.query.filter(Display.id.in_(ids)).order_by(Display.name).all() if ids else []
    if not displays:
        flash("Tick the screens to change first.", "warning")
        return redirect(url_for("displays.index"))
    form = bulk.BulkForm(admin=current_user.is_admin)
    if request.method == "POST" and request.form.get("apply") and form.validate_on_submit():
        changed = bulk.apply(displays, form, request.form)
        for d in displays:
            if d.id in changed:
                audit.record("display.update", d, detail={"changed": changed[d.id], "via": "bulk edit"})
        db.session.commit()      # ORM writes: each screen is told at once
        flash(f"{len(changed)} of {len(displays)} screen{'s' if len(displays) != 1 else ''} changed"
              + (" (the others already had these settings)." if len(changed) != len(displays) else "."), "success")
        return redirect(url_for("displays.index"))
    return render_template("displays/bulk.html", form=form, displays=displays, switches=bulk.ADMIN_SWITCHES, yes_no=bulk.YES_NO)
