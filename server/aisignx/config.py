"""Application configuration, loaded from environment variables (or a .env file).

Secrets never live in this file. Supported variables:

    AISIGNX_SECRET_KEY    session signing key (generated and stored in instance/ if unset)
    AISIGNX_DATABASE_URL  SQLAlchemy URL (default: SQLite file in instance/)
    AISIGNX_SECURE_COOKIES  "1" to mark cookies Secure (use behind HTTPS)
    AISIGNX_UPLOAD_DIR    where uploaded media is stored (default: server/uploads)
    AISIGNX_MAX_UPLOAD_MB largest accepted upload in MB (default: 2048)
    AISIGNX_FFMPEG / AISIGNX_FFPROBE  paths to ffmpeg tools (default: found on PATH)
"""
import os
import secrets
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_secret_key(instance_path: str) -> str:
    """Return the secret key from env, or a persistent random key in instance/."""
    key = os.environ.get("AISIGNX_SECRET_KEY")
    if key:
        return key
    key_file = Path(instance_path) / "secret_key"
    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip()
    key = secrets.token_hex(32)
    key_file.write_text(key, encoding="utf-8")
    return key


def build_config(instance_path: str) -> dict:
    """Build the Flask config dict. instance_path must already exist."""
    default_db = "sqlite:///" + str(Path(instance_path) / "aisignx.db").replace("\\", "/")
    default_uploads = str(Path(instance_path).parent / "uploads")
    secure = _env_flag("AISIGNX_SECURE_COOKIES")
    return {
        "UPLOAD_FOLDER": os.environ.get("AISIGNX_UPLOAD_DIR", default_uploads),
        "MAX_CONTENT_LENGTH": int(os.environ.get("AISIGNX_MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
        "FFMPEG_PATH": os.environ.get("AISIGNX_FFMPEG", "ffmpeg"),
        "FFPROBE_PATH": os.environ.get("AISIGNX_FFPROBE", "ffprobe"),
        "SECRET_KEY": load_secret_key(instance_path),
        "SQLALCHEMY_DATABASE_URI": os.environ.get("AISIGNX_DATABASE_URL", default_db),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": secure,
        "REMEMBER_COOKIE_HTTPONLY": True,
        "REMEMBER_COOKIE_SECURE": secure,
        "WTF_CSRF_TIME_LIMIT": None,
    }
