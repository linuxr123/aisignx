"""Application configuration, loaded from environment variables (or a .env file).

Secrets never live in this file. Supported variables:

    AISIGNX_SECRET_KEY    session signing key (generated and stored in instance/ if unset)
    AISIGNX_DATABASE_URL  SQLAlchemy URL (default: SQLite file in instance/)
    AISIGNX_SECURE_COOKIES  "1" to mark cookies Secure (use behind HTTPS)
    AISIGNX_UPLOAD_DIR    where uploaded media is stored (default: server/uploads)
    AISIGNX_MAX_UPLOAD_MB largest accepted upload in MB (default: 2048)
    AISIGNX_FFMPEG / AISIGNX_FFPROBE  paths to ffmpeg tools (default: found on PATH)
    AISIGNX_BROWSER       Edge/Chrome/Chromium used for web page previews (default: auto-detect)
    AISIGNX_WEBSHOT_TIMEOUT  seconds allowed per web page screenshot (default: 45)
    AISIGNX_MAX_STREAMS   most open live-update streams from screens (default: 50);
                          `flask --app wsgi serve` sizes its threads from this
    AISIGNX_SCHEDULE_TICKER  "0" turns off the per-process thread that pushes schedule
                          changes to screens at minute boundaries (default: on)
    AISIGNX_PROXY_HOPS    number of reverse proxies in front of the app whose
                          X-Forwarded-* headers are trusted (default: 0 = none)

Operations (Phase 6). Everything below is per process: run one server process.

    AISIGNX_INSTANCE_DIR  folder for the SQLite database and secret_key
                          (default: server/instance); read in create_app()
    AISIGNX_LOG_DIR       folder for aisignx.log, rotated at 5 MB x 5 files
                          (default: <instance parent>/logs)
    AISIGNX_LOG_LEVEL     DEBUG, INFO, WARNING or ERROR (default: INFO)
    AISIGNX_JOBS          "0" turns off the background jobs thread (alerts, backups,
                          retention, proof-of-play rollup) (default: on)
    AISIGNX_BACKUP_DIR    where backup archives are written (default: <instance parent>/backups)
    AISIGNX_SMTP_PASSWORD password for the alert mail server; never stored in the database
    AISIGNX_LOGIN_MAX_PER_IP    failed sign-ins per IP address per window (default: 20)
    AISIGNX_LOGIN_MAX_PER_USER  failed sign-ins per user name per window (default: 5)
    AISIGNX_LOGIN_WINDOW_MIN    length of the failed sign-in window in minutes (default: 15)
    AISIGNX_BROWSER_NO_SANDBOX  "1" starts the preview browser with --no-sandbox
                          (needed in containers that run as non-root; default: off)

Plugins (see docs/plugins.md). Per process, like everything above.

    AISIGNX_PLUGIN_CACHE_DIR  where fetched plugin data is cached
                          (default: <instance parent>/plugin-cache; never backed up)
    AISIGNX_PLUGIN_CACHE_MB   size cap of that folder in MB (default: 500)
    AISIGNX_PLUGIN_HTTP_PROXY optional http://host:port proxy for outbound plugin requests
                          (HTTPS CONNECT only; environment proxies are otherwise ignored)
    Plugin secrets (API keys) use the variable each plugin declares, for example
    AISIGNX_FINNHUB_KEY. They are read from the environment only; restart to change them.

PLUGIN_FETCH_SYNC (run plugin fetches inline), PLUGIN_DIRS (extra plugin folders) and
PLUGIN_FETCH_TEST_LOOPBACK (let the HTTP client reach a loopback test server; honoured
only with TESTING) exist for tests only and are never read from the environment.
"""
import os
import secrets
from pathlib import Path


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = (os.environ.get(name) or "").strip()
    try:
        return int(value) if value else default
    except ValueError:
        return default


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
    data_root = Path(instance_path).parent
    default_uploads = str(data_root / "uploads")
    secure = _env_flag("AISIGNX_SECURE_COOKIES")
    return {
        "UPLOAD_FOLDER": os.environ.get("AISIGNX_UPLOAD_DIR", default_uploads),
        "MAX_CONTENT_LENGTH": int(os.environ.get("AISIGNX_MAX_UPLOAD_MB", "2048")) * 1024 * 1024,
        "FFMPEG_PATH": os.environ.get("AISIGNX_FFMPEG", "ffmpeg"),
        "FFPROBE_PATH": os.environ.get("AISIGNX_FFPROBE", "ffprobe"),
        "BROWSER_PATH": os.environ.get("AISIGNX_BROWSER") or None,
        "WEBSHOT_TIMEOUT": int(os.environ.get("AISIGNX_WEBSHOT_TIMEOUT", "45")),
        "THUMBNAILS_ASYNC": True,
        "SECRET_KEY": load_secret_key(instance_path),
        "SQLALCHEMY_DATABASE_URI": os.environ.get("AISIGNX_DATABASE_URL", default_db),
        "SQLALCHEMY_TRACK_MODIFICATIONS": False,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": secure,
        "REMEMBER_COOKIE_HTTPONLY": True,
        "REMEMBER_COOKIE_SECURE": secure,
        "WTF_CSRF_TIME_LIMIT": None,
        # Screens and the player API.
        "PLAYER_HEARTBEAT_SECONDS": 30,
        "PLAYER_OFFLINE_AFTER_SECONDS": 90,
        "PLAYER_SSE_KEEPALIVE_SECONDS": 20,
        "PLAYER_SSE_MAX_AGE_SECONDS": 1800,
        "PLAYER_MAX_STREAMS": int(os.environ.get("AISIGNX_MAX_STREAMS") or 50),
        "PLAYER_SCHEDULE_TICKER": os.environ.get("AISIGNX_SCHEDULE_TICKER", "1") != "0",
        "PAIRING_CODE_TTL_SECONDS": 600,
        "PAIRING_CLAIM_SECONDS": 120,
        "PAIRING_MAX_PENDING": 50,
        "PAIRING_MAX_PENDING_PER_IP": 10,
        "PROXY_HOPS": int(os.environ.get("AISIGNX_PROXY_HOPS") or 0),
        # Operations.
        "LOG_DIR": os.environ.get("AISIGNX_LOG_DIR") or str(data_root / "logs"),
        "LOG_LEVEL": (os.environ.get("AISIGNX_LOG_LEVEL") or "INFO").strip().upper(),
        "LOG_TO_FILE": False,  # only consulted when TESTING (tests opt in)
        "JOBS_ENABLED": os.environ.get("AISIGNX_JOBS", "1").strip() != "0",
        "BACKUP_DIR": os.environ.get("AISIGNX_BACKUP_DIR") or str(data_root / "backups"),
        "SMTP_PASSWORD": os.environ.get("AISIGNX_SMTP_PASSWORD") or None,
        "LOGIN_MAX_PER_IP": _env_int("AISIGNX_LOGIN_MAX_PER_IP", 20),
        "LOGIN_MAX_PER_USER": _env_int("AISIGNX_LOGIN_MAX_PER_USER", 5),
        "LOGIN_WINDOW_SECONDS": _env_int("AISIGNX_LOGIN_WINDOW_MIN", 15) * 60,
        "BROWSER_NO_SANDBOX": _env_flag("AISIGNX_BROWSER_NO_SANDBOX"),
        # Plugins.
        "PLUGIN_CACHE_DIR": (os.environ.get("AISIGNX_PLUGIN_CACHE_DIR")
                             or str(data_root / "plugin-cache")),
        "PLUGIN_CACHE_MB": _env_int("AISIGNX_PLUGIN_CACHE_MB", 500),
        "PLUGIN_HTTP_PROXY": (os.environ.get("AISIGNX_PLUGIN_HTTP_PROXY") or "").strip() or None,
        "PLUGIN_FETCH_SYNC": False,  # tests: run fetches inline instead of on the worker
        "PLUGIN_DIRS": [],  # tests: extra folders of plugins, scanned after bundled/
    }
