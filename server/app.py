# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 AISignX contributors
import os
from flask import Flask, render_template, jsonify
from flask_migrate import Migrate
from flask_login import LoginManager

import generate_config

from logging_config import logger, configure_logging
from models import db, User, ApiToken, Display, Media, Playlist, PlaylistItem, Schedule, display_schedule, DisplayGroup, PendingDisplay, EmergencyBroadcast
from tokens import tokens_bp
from displays import displays_bp
from groups import groups_bp
from media import media_bp
from playlists import playlists_bp
from auth import auth_bp
from admin import admin_bp
from schedules import schedules_bp
from filters import register_filters
from main import main_bp
from plugin_system import plugins_bp
from display_player import player_bp
from registration import registration_bp
from audit_views import audit_bp
from domains import domains_bp, domain_switcher_state, branding_state
from settings_views import settings_bp
from werkzeug.middleware.proxy_fix import ProxyFix

# With Flask’s debug reloader, only configure in the reloaded child process.
if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not os.environ.get("FLASK_DEBUG"):
    configure_logging()

app = Flask(__name__)
register_filters(app)

config_path = os.path.join(os.path.dirname(__file__), 'config.py')
if not os.path.exists(config_path):
    generate_config.main()

app.config.from_pyfile(config_path)

# Read proxy settings from config
trust_proxy = bool(app.config.get('TRUST_PROXY', False))
proxy_hops = int(app.config.get('TRUST_PROXY_HOPS', 1))  # new: allow overriding hop count
preferred_scheme = (app.config.get('PREFERRED_URL_SCHEME') or 'http').lower()

if trust_proxy:
    # Trust proxy_hops reverse proxies (nginx=1; Cloudflare->nginx=2)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxy_hops, x_proto=proxy_hops, x_host=proxy_hops, x_port=proxy_hops)

app.config['PREFERRED_URL_SCHEME'] = preferred_scheme
# Cookie Secure flags: only force-on if the user hasn't explicitly set them.
# Setting them to False in config.py allows admins to log in over plain HTTP
# (LAN URL) while still preferring HTTPS for absolute URL generation.
if preferred_scheme == 'https':
    app.config.setdefault('SESSION_COOKIE_SECURE',  True)
    app.config.setdefault('REMEMBER_COOKIE_SECURE', True)

logger.info(f"Proxy trusted: {trust_proxy} (hops={proxy_hops}) | Preferred scheme: {preferred_scheme}")
deploy_mode = app.config.get('AISIGNX_DEPLOY_MODE') or ('https' if preferred_scheme == 'https' else 'http')
logger.info(f"Deploy mode: {deploy_mode}")
if app.config.get('SERVER_NAME'):
    logger.info(f"SERVER_NAME configured: {app.config['SERVER_NAME']}")

app.config['PLUGINS_DIR'] = os.path.join(os.path.dirname(__file__), 'plugins')
app.register_blueprint(tokens_bp)
app.register_blueprint(displays_bp)
app.register_blueprint(groups_bp)
app.register_blueprint(media_bp)
app.register_blueprint(playlists_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)
app.register_blueprint(schedules_bp)
app.register_blueprint(main_bp)
app.register_blueprint(plugins_bp)
app.register_blueprint(player_bp)
app.register_blueprint(registration_bp)
app.register_blueprint(audit_bp)
app.register_blueprint(domains_bp)
app.register_blueprint(settings_bp)


@app.context_processor
def _inject_domain_switcher():
    """Expose domain_switcher() and branding() to every template. Both
    are zero-arg helpers so the templates stay trivial."""
    import storage as _storage
    return {'domain_switcher': domain_switcher_state,
            'branding':        branding_state,
            'storage':         _storage}

# ── Downloads page ────────────────────────────────────────────────────────────
from flask import render_template as _rt, request as _req
from flask_login import login_required as _lr

@app.route('/downloads')
@_lr
def downloads_page():
    # Best-effort: determine the server's own base URL for display in the UI
    from displays import _client_setup_server_url
    host = _client_setup_server_url()
    # Load the version manifest server-side so download buttons render with
    # working hrefs even if /api/version is slow or unreachable from the page.
    from registration import _load_versions
    try:
        manifest = _load_versions() or {}
    except Exception:
        manifest = {}
    return _rt('downloads.html', server_url=host, manifest=manifest)

@app.route('/logs')
@_lr
def logs_page():
    from flask_login import current_user
    if not getattr(current_user, 'is_superadmin', False):
        return render_template('errors/404.html'), 404
    return render_template('logs.html')

@app.route('/api/logs/tail')
@_lr
def logs_tail():
    """Return the last N lines of the log file as JSON."""
    from flask_login import current_user
    if not getattr(current_user, 'is_superadmin', False):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    from logging_config import LOG_FILE
    n = min(int(_req.args.get('lines', 200)), 1000)
    try:
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
        return jsonify({'status': 'success', 'lines': [l.rstrip() for l in lines[-n:]]})
    except FileNotFoundError:
        return jsonify({'status': 'success', 'lines': []})

@app.route('/api/logs/stream')
@_lr
def logs_stream():
    """SSE endpoint that streams new log lines as they are written."""
    from flask_login import current_user
    if not getattr(current_user, 'is_superadmin', False):
        return jsonify({'status': 'error', 'message': 'Unauthorized'}), 403
    from flask import Response, stream_with_context
    import time
    from logging_config import LOG_FILE

    def generate():
        try:
            with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(0, 2)  # seek to end
                while True:
                    line = f.readline()
                    if line:
                        yield f"data: {line.rstrip()}\n\n"
                    else:
                        time.sleep(0.5)
                        yield ": ping\n\n"
        except GeneratorExit:
            pass
        except Exception:
            pass

    return Response(
        stream_with_context(generate()),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no',
        }
    )

try:
    from flask_cors import CORS
    CORS(app, resources={r"/api/*": {"origins": "*"}})
except Exception:
    pass

# ── Public health probe ──────────────────────────────────────────────────────
# Lightweight, unauthenticated, side-effect-free endpoint suitable for
# external monitors (UptimeRobot/Pingdom), reverse-proxy health checks,
# Windows service probes, and Docker/K8s liveness. Returns 200 only when
# the database is reachable; otherwise 503 so the load balancer can pull
# the node out of rotation. The richer superadmin diagnostics still live
# at /admin/system-health.
@app.route('/healthz')
def healthz():
    from flask import jsonify as _jsonify
    from sqlalchemy import text as _text
    try:
        db.session.execute(_text('SELECT 1'))
        return _jsonify({'status': 'ok'}), 200
    except Exception as exc:
        logger.warning('healthz: db check failed: %s', exc)
        return _jsonify({'status': 'error', 'message': 'db unreachable'}), 503

db.init_app(app)
migrate = Migrate(app, db)

# ── Tenant filter (Phase 1, Task 2) ──────────────────────────────────────────
# Installs a SQLAlchemy event listener that auto-filters queries on tenant
# models by current_domain_id(). Must be installed AFTER db.init_app().
from tenant_filter import (install_tenant_filter, set_current_domain_id,
                           clear_current_domain_id, current_domain_id,
                           bypass_tenant_filter)
from models import Domain, UserDomainRole
install_tenant_filter(db)

# Storage accounting (auto-updates Domain.storage_used_bytes on Media insert/
# update/delete and enforces quota at the ORM layer).
from storage import install_storage_accounting
install_storage_accounting(db)

login_manager = LoginManager(app)
login_manager.login_view = 'auth.login'

# ── Per-request tenant context ───────────────────────────────────────────────
# Resolve the active tenant from (a) session['current_domain_id'] for web
# users, or (b) the API token's domain for API requests. API token resolution
# happens later in the pipeline (in tokens.py / blueprint code) so here we
# just handle the session-based case. After-request clears it.
from flask import session, g
from flask_login import current_user as _current_user

@app.before_request
def _resolve_tenant_context():
    did = session.get('current_domain_id')
    if did is None and getattr(_current_user, 'is_authenticated', False):
        # Pick a sensible default tenant for users who have not chosen one yet
        # (e.g. immediately after login).
        with bypass_tenant_filter():
            if getattr(_current_user, 'is_superadmin', False):
                from bootstrap import default_tenant_domain_id
                did = default_tenant_domain_id()
            else:
                from permissions import has_permission
                rows = (UserDomainRole.query
                        .filter_by(user_id=_current_user.id)
                        .order_by(UserDomainRole.domain_id.asc()).all())
                for udr in rows:
                    if (has_permission(_current_user, 'domain.admin',
                                       domain_id=udr.domain_id)
                            or has_permission(_current_user, 'emergency.manage',
                                              domain_id=udr.domain_id)
                            or has_permission(_current_user, 'emergency.use',
                                              domain_id=udr.domain_id)):
                        did = udr.domain_id
                        break
                if did is None and rows:
                    did = rows[0].domain_id
            if did is not None:
                session['current_domain_id'] = did
    if did is not None:
        set_current_domain_id(did)

@app.teardown_request
def _clear_tenant_context(_exc):
    clear_current_domain_id()

for folder in ['images', 'videos', 'thumbnails']:
    path = os.path.join(app.config['UPLOAD_FOLDER'], folder)
    if not os.path.exists(path):
        os.makedirs(path)

@login_manager.user_loader
def load_user(id):
    return db.session.get(User, int(id))

# Serve the service worker from /static/sw.js with no-cache headers so browsers
# always get the latest version and don't serve a stale SW. The
# Service-Worker-Allowed header lifts the path restriction so the SW can
# cover plugin URLs (/plugin/, /plugin_assets/) outside its own /static/ folder.
@app.route('/static/sw.js')
def service_worker():
    from flask import send_from_directory, make_response
    resp = make_response(send_from_directory('static', 'sw.js'))
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Content-Type']  = 'application/javascript'
    resp.headers['Service-Worker-Allowed'] = '/'
    return resp

@app.errorhandler(404)
def not_found_error(error):
    return render_template('errors/404.html'), 404

@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return render_template('errors/500.html'), 500

def init_db():
    """First-boot init. Creates schema (db.create_all is idempotent), seeds
    the initial superadmin if no users exist, then runs the full bootstrap
    (permissions, system roles, default domain). Safe to re-run on every start.

    Set AISIGNX_SKIP_INIT_DB=1 to skip (used by alembic migration generation)."""
    if os.environ.get('AISIGNX_SKIP_INIT_DB'):
        return
    with app.app_context():
        db.create_all()
        import bootstrap as _bootstrap
        _bootstrap._evolve_schema()
        with bypass_tenant_filter():
            if User.query.count() == 0:
                admin = User(username='admin', email='admin@example.com',
                             is_superadmin=True, active=True)
                admin.set_password('Admin123!')
                db.session.add(admin)
                db.session.commit()
                logger.info('Created default superadmin: admin/Admin123!')
    # Run full bootstrap (idempotent). Schema evolution already ran above.
    _bootstrap.run(app)

init_db()

# ── Background jobs + heartbeat batching (Phase 1, Task 7) ────────────────────
# Started AFTER init_db() so settings are seeded and the schema exists. The
# Flask debug reloader runs init twice; jobs.start() is idempotent.
import jobs
import heartbeat
jobs.start(app, worker_count=2)
heartbeat.install(app)

# Disk-space monitor: periodic free-space probe + threshold audit log.
# Must run after jobs.start() because it registers a periodic job.
import disk_monitor
with app.app_context():
    disk_monitor.start()

# Audit log retention: periodic prune of old AuditLog rows (Phase 4).
import audit_retention
with app.app_context():
    audit_retention.install()

# Scheduled backups: periodic global backup honoring backup.schedule.*
# settings. The job itself no-ops while backup.schedule.enabled is false,
# so install is always safe.
import backup_scheduler
with app.app_context():
    backup_scheduler.install()

# Proof-of-Play retention: periodic prune of old ProofOfPlay rows (Phase 4).
import proof_of_play as _pop
with app.app_context():
    _pop.install()

# Display offline/recovery alerts (email + webhook). Disabled until an
# admin sets `alerts.enabled = true` in system settings; the sweep still
# runs but no-ops.
import alerts as _alerts
with app.app_context():
    _alerts.install()

if __name__ == '__main__':
    app.config['DEBUG'] = True
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    logger.info(f"Starting Digital Signage Server on http://0.0.0.0:5000")
    logger.info(f"Environment: {'Development' if app.config['DEBUG'] else 'Production'}")
    app.run(host='0.0.0.0', port=5000, debug=True)