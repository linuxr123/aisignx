"""Flask extension instances, created once and bound in create_app()."""
import sqlite3

from flask_login import LoginManager
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from sqlalchemy import event
from sqlalchemy.engine import Engine

db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message_category = "warning"


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """Per-connection SQLite settings.

    - foreign_keys: SQLite ignores ON DELETE CASCADE / SET NULL unless switched on.
    - busy_timeout: wait for a lock instead of failing at once (heartbeats write while
      admins commit).
    - journal_mode=WAL: readers don't block the writer. WAL is unsuitable for network
      shares; the database file must be on a local disk.
    """
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()
