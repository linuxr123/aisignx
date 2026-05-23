# Copy to config.py for local development, or run: python generate_config.py
# Never commit config.py — it may contain secrets.
#
# Easiest path: python generate_config.py --interactive
# Quick switch: change AISIGNX_DEPLOY_MODE to 'http' or 'https' and restart.

import os

from deploy_modes import resolve_deploy_settings

# 'http'  = direct HTTP on port 5000 (LAN / dev)
# 'https' = TLS at nginx/Caddy/IIS; AISignX on http://127.0.0.1:5000
AISIGNX_DEPLOY_MODE = os.environ.get('AISIGNX_DEPLOY_MODE', 'http')

_deploy = resolve_deploy_settings(
    AISIGNX_DEPLOY_MODE,
    proxy_hops=int(os.environ.get('TRUST_PROXY_HOPS', '1')),
    server_name=os.environ.get('AISIGNX_SERVER_NAME') or None,
)

SECRET_KEY = os.environ.get('AISIGNX_SECRET_KEY', 'dev-only-change-me')

_db_path = os.environ.get('AISIGNX_DB_PATH')
SQLALCHEMY_DATABASE_URI = (
    os.environ.get('AISIGNX_DATABASE_URI')
    or (f'sqlite:///{_db_path}' if _db_path else 'sqlite:///digital_signage.db')
)
SQLALCHEMY_TRACK_MODIFICATIONS = False
UPLOAD_FOLDER = os.environ.get('UPLOAD_FOLDER', 'uploads')
MAX_CONTENT_LENGTH = 100 * 1024 * 1024  # 100 MB
PLUGINS_DIR = os.environ.get('PLUGINS_DIR', 'plugins')

TRUST_PROXY = _deploy['TRUST_PROXY']
TRUST_PROXY_HOPS = int(os.environ.get('TRUST_PROXY_HOPS', str(_deploy['TRUST_PROXY_HOPS'])))
PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', _deploy['PREFERRED_URL_SCHEME'])
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', str(_deploy['SESSION_COOKIE_SECURE']).lower()).lower() in ('1', 'true', 'yes')
REMEMBER_COOKIE_SECURE = os.environ.get('REMEMBER_COOKIE_SECURE', str(_deploy['REMEMBER_COOKIE_SECURE']).lower()).lower() in ('1', 'true', 'yes')

SERVER_NAME = os.environ.get('AISIGNX_SERVER_NAME') or _deploy.get('SERVER_NAME')
