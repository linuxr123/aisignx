from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from ..models import User
from . import bp
from .forms import ChangePasswordForm, LoginForm

_DUMMY_HASH = generate_password_hash("timing-equalizer")


def _safe_next(target: str | None) -> str | None:
    """Only allow relative redirect targets on this site."""
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user is None:
            # Spend the same hashing time as a real check so usernames can't be probed.
            check_password_hash(_DUMMY_HASH, form.password.data)
        if user is None or not user.check_password(form.password.data) or not user.is_active:
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html", form=form), 401
        user.last_login_at = datetime.now(timezone.utc)
        db.session.commit()
        login_user(user, remember=form.remember.data)
        return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))
    return render_template("auth/login.html", form=form)


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    flash("You have been signed out.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
        else:
            current_user.set_password(form.password.data)
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", form=form)
