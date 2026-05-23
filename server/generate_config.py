import os
import secrets

CONFIG_PATH = os.path.join(os.path.dirname(__file__), 'config.py')
EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), 'config.example.py')


def generate_secret_key():
    return secrets.token_urlsafe(32)


def create_config():
    """Create a local config.py (gitignored) with a random dev secret."""
    secret = generate_secret_key()
    default_config = f"""# Auto-generated local config — do not commit (see config.example.py)
import os

SECRET_KEY = os.environ.get('AISIGNX_SECRET_KEY', '{secret}')

_db_path = os.environ.get('AISIGNX_DB_PATH')
SQLALCHEMY_DATABASE_URI = (
    os.environ.get('AISIGNX_DATABASE_URI')
    or (f'sqlite:///{{_db_path}}' if _db_path else 'sqlite:///digital_signage.db')
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
"""
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        f.write(default_config)
    print(f"Config file created at {CONFIG_PATH}")


def main():
    if os.path.exists(CONFIG_PATH):
        print(f"Config file already exists at {CONFIG_PATH}")
        return
    if os.path.exists(EXAMPLE_PATH):
        import shutil
        shutil.copy(EXAMPLE_PATH, CONFIG_PATH)
        print(f"Copied {EXAMPLE_PATH} -> {CONFIG_PATH}")
        print("Set AISIGNX_SECRET_KEY in the environment for production, or edit config.py for local dev.")
        return
    create_config()


if __name__ == '__main__':
    main()
