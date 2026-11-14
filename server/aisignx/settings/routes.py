from flask import flash, redirect, render_template, url_for

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..extensions import db
from ..models import Setting
from ..player.broker import get_broker
from . import bp
from .forms import SettingsForm, timezone_choices


@bp.route("/", methods=["GET", "POST"])
@admin_required
def index():
    form = SettingsForm()
    form.timezone.choices = timezone_choices()
    if form.validate_on_submit():
        tz_changed = form.timezone.data != Setting.get("timezone")
        new = {"org_name": form.org_name.data.strip(), "timezone": form.timezone.data,
               "default_duration": str(form.default_duration.data)}
        changed = [k for k, v in new.items() if str(Setting.get(k)) != v]
        for key, value in new.items():
            Setting.set(key, value)
        if changed:
            audit.record("settings.update", detail={"changed": changed})
        db.session.commit()
        if tz_changed:  # schedules now fall at different instants
            broker = get_broker()
            if broker is not None:
                broker.publish_all("manifest", {})
        flash("Settings saved.", "success")
        return redirect(url_for("settings.index"))
    if not form.is_submitted():
        form.org_name.data = Setting.get("org_name")
        form.timezone.data = Setting.get("timezone")
        form.default_duration.data = int(Setting.get("default_duration"))
    return render_template("settings/index.html", form=form)
