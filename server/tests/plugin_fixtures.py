"""Shared fixtures for the plugin tests: an app that also loads the echo fixture plugins
(tests/fixtures/plugins) and keeps its plugin cache in tmp_path.

Import the fixtures into a test module: from .plugin_fixtures import papp, padmin, ...
"""
from pathlib import Path

import pytest
from flask_migrate import upgrade

from aisignx import create_app
from aisignx.extensions import db

from .conftest import DISPLAY_TOKEN, login, make_user

FIXTURES = Path(__file__).parent / "fixtures" / "plugins"
ECHO_CONFIG = {"message": "Hello", "note": "keep-me-private", "tz": "", "place": None,
               "images": [], "count": 3, "shout": False, "color": "red", "link": "",
               "body": "", "refresh": 60}


def make_plugin_app(tmp_path, monkeypatch, **overrides):
    monkeypatch.setenv("AISIGNX_SECRET_KEY", "test-secret")
    config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'plugins.db').as_posix()}",
        "WTF_CSRF_ENABLED": False,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "THUMBNAILS_ASYNC": False,
        "BROWSER_PATH": "aisignx-test-no-browser",
        "PLAYER_SCHEDULE_TICKER": False,
        "JOBS_ENABLED": False,
        "BACKUP_DIR": str(tmp_path / "backups"),
        "LOG_DIR": str(tmp_path / "logs"),
        "PLUGIN_DIRS": [str(FIXTURES)],
        "PLUGIN_CACHE_DIR": str(tmp_path / "plugin-cache"),
        "PLUGIN_FETCH_SYNC": True,
    }
    config.update(overrides)
    app = create_app(config)
    with app.app_context():
        upgrade()
    return app


@pytest.fixture
def papp(tmp_path, monkeypatch):
    app = make_plugin_app(tmp_path, monkeypatch)
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def pclient(papp):
    return papp.test_client()


@pytest.fixture
def padmin(papp, pclient):
    make_user(papp, "admin", "admin")
    login(pclient, "admin")
    return pclient


def echo_hooks(app):
    return app.extensions["aisignx_plugins"].get("echo").hooks


def add_item(app, key="echo", config=None, name="Echo item", duration=None):
    """Create a plugin item through the service. Returns its media id."""
    from aisignx.plugins import service

    with app.test_request_context():
        plugin = service.get_plugin(key)
        raw = dict(ECHO_CONFIG if key == "echo" else {}, **(config or {}))
        cfg, errors = service.validate_config(plugin, raw)
        assert not errors, errors
        media = service.create_item(plugin, name, duration, cfg, None)
        db.session.commit()
        return media.id


def paired_with(app, *media_ids):
    """A paired display whose default playlist holds the given media. Returns the token."""
    from aisignx.models import Display, Media, Playlist, PlaylistItem
    from aisignx.player.auth import hash_token
    from aisignx.timeutil import utcnow

    with app.app_context():
        playlist = Playlist(name="P")
        db.session.add(playlist)
        for pos, mid in enumerate(media_ids):
            media = db.session.get(Media, mid)
            playlist.items.append(PlaylistItem(media=media, position=pos))
        display = Display(name="Lobby", token_hash=hash_token(DISPLAY_TOKEN),
                          paired_at=utcnow(), playlist=playlist)
        db.session.add(display)
        db.session.commit()
    return DISPLAY_TOKEN
