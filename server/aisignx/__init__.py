"""AiSignX: self-hosted digital signage (single organization)."""
import os

from dotenv import load_dotenv
from flask import Flask, redirect, request, url_for

from .config import build_config
from .extensions import csrf, db, login_manager, migrate

__version__ = "3.0.0.dev0"

# Endpoints reachable before the first admin account exists.
_SETUP_EXEMPT = {"setup.wizard", "static", "main.healthz"}


def create_app(test_config: dict | None = None) -> Flask:
    load_dotenv()
    app = Flask(__name__, instance_relative_config=True)
    os.makedirs(app.instance_path, exist_ok=True)

    app.config.update(build_config(app.instance_path))
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(app.root_path), "migrations"))
    login_manager.init_app(app)
    csrf.init_app(app)

    from . import models  # noqa: F401  (register models with SQLAlchemy)

    _register_blueprints(app)
    _register_setup_guard(app)
    _register_template_context(app)
    _register_error_pages(app)
    return app


def _register_blueprints(app: Flask) -> None:
    from .auth import bp as auth_bp
    from .main import bp as main_bp
    from .media import bp as media_bp
    from .playlists import bp as playlists_bp
    from .settings import bp as settings_bp
    from .setup import bp as setup_bp
    from .users import bp as users_bp

    for bp in (auth_bp, main_bp, media_bp, playlists_bp, settings_bp, setup_bp, users_bp):
        app.register_blueprint(bp)


def _register_setup_guard(app: Flask) -> None:
    """Send every request to the setup wizard until an admin account exists."""
    from .setup.service import setup_required

    @app.before_request
    def require_setup():
        if request.endpoint in _SETUP_EXEMPT:
            return None
        if setup_required():
            return redirect(url_for("setup.wizard"))
        return None


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
        return {"org_name": org_name, "app_version": __version__}


def _register_error_pages(app: Flask) -> None:
    from flask import flash, redirect, render_template, url_for

    @app.errorhandler(413)
    def too_large(_e):
        limit_mb = app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024)
        flash(f"Upload too large. The limit is {limit_mb} MB.", "danger")
        return redirect(url_for("media.index"), 303)

    @app.errorhandler(403)
    def forbidden(_e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(_e):
        return render_template("errors/404.html"), 404
