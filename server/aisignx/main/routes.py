from flask import redirect, render_template, url_for
from flask_login import current_user, login_required

from ..models import Media, Playlist, User
from . import bp


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    counts = {
        "Media": Media.query.count(),
        "Playlists": Playlist.query.count(),
        "Users": User.query.count(),
    }
    return render_template("main/dashboard.html", counts=counts)


@bp.route("/healthz")
def healthz():
    return {"status": "ok"}
