from flask import current_app, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy import text

from ..displays.service import dashboard_counts
from ..emergency.service import live as live_emergencies
from ..extensions import db
from ..models import DisplayGroup, Media, Playlist, Schedule, User
from ..system import run_checks
from . import bp


@bp.route("/")
def index():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    return redirect(url_for("auth.login"))


@bp.route("/dashboard")
@login_required
def dashboard():
    displays, online = dashboard_counts()
    counts = {
        "Displays": displays,
        "Online": online,
        "Media": Media.query.count(),
        "Playlists": Playlist.query.count(),
        "Enabled schedules": Schedule.query.filter(Schedule.enabled.is_(True)).count(),
        "Groups": DisplayGroup.query.count(),
        "Users": User.query.count(),
    }
    problems = [c for c in run_checks() if not c.ok] if current_user.is_admin else []
    return render_template("main/dashboard.html", counts=counts, problems=problems,
                           emergencies=live_emergencies())


@bp.route("/healthz")
def healthz():
    # Kiosk clients probe this in setup to tell an AiSignX server from anything else, and
    # Docker / monitoring use it for liveness. "api" is the player API generation; no
    # version string or path, so nothing is disclosed. 503 when the database is down.
    body = {"status": "ok", "app": "aisignx", "api": 1}
    try:
        db.session.execute(text("SELECT 1"))
    except Exception:
        current_app.logger.exception("Health check: the database did not answer")
        try:
            db.session.rollback()
        except Exception:
            pass
        return {**body, "status": "error"}, 503
    return body
