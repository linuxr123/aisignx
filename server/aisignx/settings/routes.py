from flask import flash, redirect, render_template, url_for

from ..auth.decorators import admin_required
from ..extensions import db
from ..models import Setting
from . import bp
from .forms import SettingsForm, timezone_choices


@bp.route("/", methods=["GET", "POST"])
@admin_required
def index():
    form = SettingsForm()
    form.timezone.choices = timezone_choices()
    if form.validate_on_submit():
        Setting.set("org_name", form.org_name.data.strip())
        Setting.set("timezone", form.timezone.data)
        db.session.commit()
        flash("Settings saved.", "success")
        return redirect(url_for("settings.index"))
    if not form.is_submitted():
        form.org_name.data = Setting.get("org_name")
        form.timezone.data = Setting.get("timezone")
    return render_template("settings/index.html", form=form)
