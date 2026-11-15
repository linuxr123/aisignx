"""Updates page (admin): what is installed, what is published, release notes, check now."""
from flask import flash, redirect, render_template, url_for
from flask_wtf import FlaskForm

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..extensions import db
from ..models import Setting
from . import bp, service


class ActionForm(FlaskForm):
    """CSRF only."""


@bp.route("/")
@admin_required
def index():
    return render_template("updates/index.html", st=service.status(), form=ActionForm())


@bp.route("/check", methods=["POST"])
@admin_required
def check_now():
    if not ActionForm().validate_on_submit():
        flash("The page expired; try again.", "warning")
    elif not service.enabled():
        flash("The update check is switched off.", "info")
    else:
        flash("Checked: " + service.check() + ".", "info")
    return redirect(url_for("updates.index"))


@bp.route("/toggle", methods=["POST"])
@admin_required
def toggle():
    if ActionForm().validate_on_submit():
        new = "0" if Setting.get_bool("updates.enabled") else "1"
        Setting.set("updates.enabled", new)
        audit.record("updates.toggle", detail={"enabled": new == "1"})
        db.session.commit()
        flash("Update check " + ("on." if new == "1" else "off."), "info")
    return redirect(url_for("updates.index"))
