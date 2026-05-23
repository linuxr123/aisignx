# Copy to config.py for local development, or rely on generate_config.py on first run.
# Never commit config.py — it may contain secrets.
#
#   cp config.example.py config.py
#   # Set AISIGNX_SECRET_KEY in the environment, or edit SECRET_KEY below for dev only.

import os

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

TRUST_PROXY = os.environ.get('TRUST_PROXY', 'true').lower() in ('1', 'true', 'yes')
TRUST_PROXY_HOPS = int(os.environ.get('TRUST_PROXY_HOPS', '1'))
PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'https')

SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() in ('1', 'true', 'yes')
REMEMBER_COOKIE_SECURE = os.environ.get('REMEMBER_COOKIE_SECURE', 'false').lower() in ('1', 'true', 'yes')
