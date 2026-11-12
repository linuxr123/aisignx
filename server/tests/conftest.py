import re

import pytest
from flask_migrate import upgrade

from aisignx import create_app
from aisignx.extensions import db
from aisignx.models import User

PASSWORD = "correct-horse-battery"


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("AISIGNX_SECRET_KEY", "test-secret")
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        "WTF_CSRF_ENABLED": False,
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "THUMBNAILS_ASYNC": False,  # run thumbnail jobs inline so tests are deterministic
        "BROWSER_PATH": "aisignx-test-no-browser",  # no real browser/network unless a test opts in
    })
    with app.app_context():
        upgrade()  # build the schema through Alembic, exactly like production
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def make_user(app, username, role, password=PASSWORD, active=True):
    with app.app_context():
        user = User(username=username, role=role, is_active_flag=active)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        return user.id


def login(client, username, password=PASSWORD):
    return client.post("/login", data={"username": username, "password": password})


@pytest.fixture
def configured(app):
    """An app past first-run setup (an admin account exists)."""
    make_user(app, "admin", "admin")
    return app


@pytest.fixture
def admin_client(configured, client):
    login(client, "admin")
    return client


def png_bytes(size=(64, 36), color=(200, 30, 30)) -> bytes:
    from io import BytesIO

    from PIL import Image

    buf = BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def upload(client, *files):
    """files: (filename, bytes) pairs."""
    from io import BytesIO

    data = {"files": [(BytesIO(content), name) for name, content in files]}
    return client.post("/media/upload", data=data, content_type="multipart/form-data")


def csrf_token_from(html: str) -> str:
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert match, "no CSRF token in page"
    return match.group(1)


# --- Screens (Phase 3) ------------------------------------------------------------------

DISPLAY_TOKEN = "test-display-token-0123456789abcdefghijklmnopq"


def bearer(token: str) -> dict:
    """Request headers carrying a display token."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def paired_display(app):
    """A paired Display (no playlist) with a known token. Returns (display_id, token)."""
    from aisignx.models import Display
    from aisignx.player.auth import hash_token
    from aisignx.timeutil import utcnow

    with app.app_context():
        display = Display(name="Lobby", token_hash=hash_token(DISPLAY_TOKEN), paired_at=utcnow())
        db.session.add(display)
        db.session.commit()
        return display.id, DISPLAY_TOKEN


def make_csrf_app(tmp_path, monkeypatch, **overrides):
    """An app with CSRF protection ON (the default fixture turns it off)."""
    monkeypatch.setenv("AISIGNX_SECRET_KEY", "test-secret")
    config = {
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'csrf.db').as_posix()}",
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "THUMBNAILS_ASYNC": False,
        "BROWSER_PATH": "aisignx-test-no-browser",
    }
    config.update(overrides)
    app = create_app(config)
    with app.app_context():
        upgrade()
    return app
