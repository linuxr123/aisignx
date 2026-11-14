import math
from datetime import datetime, timezone
from urllib.parse import urlsplit

from flask import flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from werkzeug.security import check_password_hash, generate_password_hash

from ..audit import service as audit
from ..extensions import db
from ..models import User
from . import bp, ratelimit
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
        ip = request.remote_addr
        ukey = ratelimit.user_key(form.username.data)
        blocked = ratelimit.login_blocked(ip, ukey)
        if blocked:
            return _rate_limited(form, ukey, *blocked)

        user = User.query.filter_by(username=form.username.data.strip()).first()
        if user is None:
            # Spend the same hashing time as a real check so usernames can't be probed.
            check_password_hash(_DUMMY_HASH, form.password.data)
        if user is None or not user.check_password(form.password.data) or not user.is_active:
            ratelimit.login_failed(ip, ukey)
            audit.record_now("auth.login_failed", detail={"username": ukey[:64]})
            flash("Invalid username or password.", "danger")
            return render_template("auth/login.html", form=form), 401
        ratelimit.login_succeeded(ukey)
        user.last_login_at = datetime.now(timezone.utc)
        audit.record("auth.login", target=user, user=user)
        db.session.commit()
        login_user(user, remember=form.remember.data)
        return redirect(_safe_next(request.args.get("next")) or url_for("main.dashboard"))
    return render_template("auth/login.html", form=form)


def _rate_limited(form, ukey: str, wait_s: int, scope: str):
    """429 for a blocked sign-in. Identical whether or not the user name exists."""
    limiter = ratelimit.limiters()[scope]
    if limiter.should_log(ratelimit.ip_key(request.remote_addr) if scope == "ip" else ukey):
        audit.record_now("auth.rate_limited", detail={"username": ukey[:64], "scope": scope})
    minutes = max(1, math.ceil(wait_s / 60))
    flash(f"Too many failed sign-in attempts. Try again in {minutes} "
          f"minute{'s' if minutes != 1 else ''}.", "danger")
    resp = make_response(render_template("auth/login.html", form=form), 429)
    resp.headers["Retry-After"] = str(wait_s)
    return resp


@bp.route("/logout", methods=["POST"])
@login_required
def logout():
    audit.record_now("auth.logout")
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
            audit.record("auth.password_change", target=current_user._get_current_object())
            db.session.commit()
            flash("Password changed.", "success")
            return redirect(url_for("auth.profile"))
    return render_template("auth/profile.html", form=form)
