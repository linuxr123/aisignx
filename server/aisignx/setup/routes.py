from flask import abort, flash, redirect, render_template, url_for
from flask_login import login_user

from ..extensions import db
from ..models import Setting, User
from ..models.user import ROLE_ADMIN
from ..settings.forms import timezone_choices
from . import bp
from .forms import SetupForm
from .service import setup_required


@bp.route("/", methods=["GET", "POST"])
def wizard():
    if not setup_required():
        abort(404)

    form = SetupForm()
    form.timezone.choices = timezone_choices()
    if form.validate_on_submit():
        admin = User(
            username=form.username.data.strip(),
            email=(form.email.data or "").strip() or None,
            role=ROLE_ADMIN,
        )
        admin.set_password(form.password.data)
        db.session.add(admin)
        Setting.set("org_name", form.org_name.data.strip())
        Setting.set("timezone", form.timezone.data)
        db.session.commit()
        login_user(admin)
        flash("Setup complete. Welcome to AiSignX!", "success")
        return redirect(url_for("main.dashboard"))
    return render_template("setup/wizard.html", form=form)
