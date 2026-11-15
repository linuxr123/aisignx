"""API keys entered on the Plugins page: stored encrypted, env wins, never echoed."""
import pytest

from aisignx.extensions import db
from aisignx.models import AuditLog, Setting
from aisignx.plugins import service
from aisignx.plugins.registry import get_plugin

from .conftest import login, make_user
from .plugin_fixtures import make_plugin_app

KEY = "fh-secret-key-123"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.delenv("AISIGNX_FINNHUB_KEY", raising=False)
    app = make_plugin_app(tmp_path, monkeypatch)
    app.config["AISIGNX_FINNHUB_KEY"] = ""
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def admin(app):
    make_user(app, "admin", "admin")
    client = app.test_client()
    login(client, "admin")
    return client


def _save(client, **form):
    return client.post("/plugins/stocks/settings", data={"enabled": "1", **form})


def _value(app):
    with app.app_context():
        return service.secret_value(get_plugin("stocks"), "finnhub_key")


def test_admin_saves_key_encrypted_and_never_echoes_it(app, admin):
    assert "Paste your key" in admin.get("/plugins/").get_data(as_text=True)
    assert _save(admin, **{"secret-finnhub_key": KEY}).status_code == 303
    assert _value(app) == KEY
    with app.app_context():
        stored = Setting.get("plugins.stocks.secret.finnhub_key")
        assert stored and KEY not in stored
        detail = str(AuditLog.query.filter_by(action="plugin.settings").all()[-1].detail)
        assert KEY not in detail
    html = admin.get("/plugins/").get_data(as_text=True)
    assert KEY not in html and "Saved. Type a new key" in html and "configured" in html


def test_blank_field_keeps_key_and_checkbox_removes_it(app, admin):
    _save(admin, **{"secret-finnhub_key": KEY})
    _save(admin, **{"secret-finnhub_key": ""})
    assert _value(app) == KEY
    _save(admin, **{"clear-secret-finnhub_key": "1"})
    assert _value(app) is None


def test_environment_variable_wins(app, admin):
    _save(admin, **{"secret-finnhub_key": KEY})
    app.config["AISIGNX_FINNHUB_KEY"] = "from-env"
    assert _value(app) == "from-env"
    html = admin.get("/plugins/").get_data(as_text=True)
    assert "environment variable takes precedence" in html and "from-env" not in html


def test_invalid_key_is_refused(app, admin):
    _save(admin, **{"secret-finnhub_key": "has space"})
    assert _value(app) is None


def test_changed_secret_key_means_missing_not_a_crash(app, admin):
    _save(admin, **{"secret-finnhub_key": KEY})
    app.config["SECRET_KEY"] = "another-instance"
    assert _value(app) is None
    login(admin, "admin")  # sessions are signed with the old key
    assert admin.get("/plugins/").status_code == 200


def test_editor_cannot_save_a_key(app):
    make_user(app, "ed", "editor")
    c = app.test_client()
    login(c, "ed")
    assert _save(c, **{"secret-finnhub_key": KEY}).status_code in (302, 303, 403)
    assert _value(app) is None


def test_plugin_runner_files_are_cacheable_for_offline_starts(app):
    c = app.test_client()
    r = c.get("/static/player/plugins/rss/index.html")
    assert r.status_code == 200 and "max-age=3600" in r.headers["Cache-Control"]
    other = c.get("/static/player/player.js")
    assert "max-age=3600" not in (other.headers.get("Cache-Control") or "")
