"""Facts about the database: dialect, migration revisions and the SQLite file."""
from pathlib import Path

from flask import current_app
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .extensions import db


def dialect() -> str:
    """"sqlite", "postgresql", ..."""
    return db.engine.dialect.name


def current_revision() -> str | None:
    """The revision recorded in alembic_version, or None (no table / empty)."""
    try:
        with db.engine.connect() as conn:
            return conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except SQLAlchemyError:
        return None


def _scripts():
    from alembic.script import ScriptDirectory

    config = current_app.extensions["migrate"].migrate.get_config()
    return ScriptDirectory.from_config(config)


def head_revision() -> str | None:
    """The newest revision this version of AiSignX ships."""
    return _scripts().get_current_head()


def known_revisions() -> set[str]:
    """Every revision this version of AiSignX knows (a restore refuses anything else)."""
    return {script.revision for script in _scripts().walk_revisions()}


def sqlite_path() -> Path | None:
    """The absolute path of the SQLite database file, or None for other databases
    and in-memory SQLite."""
    url = db.engine.url
    if url.get_backend_name() != "sqlite":
        return None
    database = url.database
    if not database or database == ":memory:" or database.startswith("file:"):
        return None
    return Path(database).resolve()
