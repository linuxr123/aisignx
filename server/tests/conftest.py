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


def csrf_token_from(html: str) -> str:
    match = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    assert match, "no CSRF token in page"
    return match.group(1)
