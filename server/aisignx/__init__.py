"""AiSignX: self-hosted digital signage (single organization)."""
import os

from dotenv import load_dotenv
from flask import Flask, redirect, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import build_config
from .extensions import csrf, db, login_manager, migrate

__version__ = "3.0.0.dev0"

# Endpoints reachable before the first admin account exists.
_SETUP_EXEMPT = {"setup.wizard", "static", "main.healthz"}
# Screens are never redirected to the wizard; the player API answers 503 setup_required.
_SETUP_EXEMPT_BLUEPRINTS = {"player", "player_api"}


def _is_api_request() -> bool:
    return request.path.startswith("/api/")


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    instance_dir = os.environ.get("AISIGNX_INSTANCE_DIR")
    app = Flask(__name__, instance_relative_config=True,
                instance_path=os.path.abspath(instance_dir) if instance_dir else None)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(build_config(app.instance_path))
    if test_config:
        app.config.update(test_config)

    from .logging_setup import configure_logging
    configure_logging(app)

    hops = int(app.config.get("PROXY_HOPS") or 0)
    if hops > 0:  # trust X-Forwarded-* from exactly this many reverse proxies
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=hops, x_proto=hops, x_host=hops)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(app.root_path), "migrations"))
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401  (register models with SQLAlchemy)

    from . import maintenance
    maintenance.register(app)  # first: answers 503 before any other hook runs

    _register_blueprints(app)
    from . import player, plugins, serve, system
    from .backup import cli as backup_cli
    plugins.init_app(app)  # before the player: a bad plugin.json fails start-up here
    player.init_app(app)
    system.register(app)
    serve.register(app)
    backup_cli.register(app)
    _register_setup_guard(app)
    _register_jobs_starter(app)
    _register_template_context(app)
    _register_error_pages(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from .alerts import bp as alerts_bp
    from .api import bp as api_bp
    from .audit import bp as audit_bp
    from .auth import bp as auth_bp
    from .backup import bp as backup_bp
    from .displays import bp as displays_bp
    from .downloads import bp as downloads_bp
    from .emergency import bp as emergency_bp
    from .groups import bp as groups_bp
    from .main import bp as main_bp
    from .media import bp as media_bp
    from .player import api_bp as player_api_bp
    from .player import page_bp as player_bp
    from .playlists import bp as playlists_bp
    from .plugins import bp as plugins_bp
    from .pop import bp as pop_bp
    from .schedules import bp as schedules_bp
    from .settings import bp as settings_bp
    from .setup import bp as setup_bp
    from .system import bp as system_bp
    from .tokens import bp as tokens_bp
    from .users import bp as users_bp

    # The player API authenticates with display tokens and the JSON API with Bearer API
    # tokens, never with the session cookie, so both are exempt from CSRF. Admin forms
    # keep CSRF.
    csrf.exempt(player_api_bp)
    csrf.exempt(api_bp)
    for bp in (alerts_bp, api_bp, audit_bp, auth_bp, backup_bp, displays_bp, downloads_bp, emergency_bp,
               groups_bp, main_bp, media_bp, player_bp, player_api_bp, playlists_bp, plugins_bp,
               pop_bp, schedules_bp, settings_bp, setup_bp, system_bp, tokens_bp, users_bp):
        app.register_blueprint(bp)


def _register_setup_guard(app: Flask) -> None:
    """Send every request to the setup wizard until an admin account exists."""
    from .setup.service import setup_required

    @app.before_request
    def require_setup():
        if (request.endpoint in _SETUP_EXEMPT or request.blueprint in _SETUP_EXEMPT_BLUEPRINTS
                or _is_api_request()):
            return None
        if setup_required():
            return redirect(url_for("setup.wizard"))
        return None


def _register_jobs_starter(app: Flask) -> None:
    """Start the background jobs thread on the first request (cheap once running).
    `flask --app wsgi serve` starts it up front; create_app() itself never does."""
    from . import jobs

    @app.before_request
    def start_jobs():
        jobs.ensure_started(app)


def _open_alert_count() -> int:
    """Open, unacknowledged alerts for the nav badge (signed-in users only)."""
    from flask_login import current_user

    from .models import Alert

    if not current_user or not current_user.is_authenticated:
        return 0
    try:
        return Alert.query.filter(Alert.resolved_at.is_(None),
                                  Alert.acknowledged_at.is_(None)).count()
    except Exception:  # DB not migrated yet
        db.session.rollback()
        return 0


def _register_template_context(app: Flask) -> None:
    from . import filters
    from .models import Setting

    filters.register(app)

    @app.context_processor
    def inject_globals():
        try:
            org_name = Setting.get("org_name")
        except Exception:  # DB not migrated yet
            org_name = "AiSignX"
        return {"org_name": org_name, "app_version": __version__,
                "open_alert_count": _open_alert_count()}


def _register_error_pages(app: Flask) -> None:
    """HTML error pages for the admin; JSON for /api/ (routing 404/405 never reach
    blueprint handlers, and these code-specific handlers would win over them)."""
    from flask import flash, redirect, render_template, url_for

    from .player.api import http_error_response

    @app.errorhandler(413)
    def too_large(e):
        if _is_api_request():
            return http_error_response(e)
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(f"Upload too large. The limit is {limit_mb} MB.", "danger")
        return redirect(url_for("media.index"), 303)

    @app.errorhandler(403)
    def forbidden(e):
        if _is_api_request():
            return http_error_response(e)
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        if _is_api_request():
            return http_error_response(e)
        return render_template("errors/404.html"), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        if _is_api_request():
            return http_error_response(e)
        return e
