from aisignx import create_app

from .conftest import PASSWORD, csrf_token_from, login, make_user


def test_login_logout(app, client, configured):
    make_user(app, "alice", "viewer")
    resp = login(client, "alice")
    assert resp.status_code == 302
    assert client.get("/dashboard").status_code == 200

    assert client.get("/logout").status_code == 405  # logout is POST-only
    client.post("/logout")
    resp = client.get("/dashboard")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_bad_password_and_unknown_user(app, client, configured):
    make_user(app, "alice", "viewer")
    assert login(client, "alice", "wrong-password").status_code == 401
    assert login(client, "nobody").status_code == 401


def test_disabled_user_cannot_login(app, client, configured):
    make_user(app, "bob", "editor", active=False)
    assert login(client, "bob").status_code == 401


def test_next_redirect_only_allows_local_paths(app, client, configured):
    make_user(app, "alice", "viewer")
    data = {"username": "alice", "password": PASSWORD}
    resp = client.post("/login?next=https://evil.example/", data=data)
    assert resp.headers["Location"].endswith("/dashboard")
    client.post("/logout")
    resp = client.post("/login?next=/auth-ok", data=data)
    assert resp.headers["Location"].endswith("/auth-ok")


def test_change_password(app, client, configured):
    make_user(app, "alice", "viewer")
    login(client, "alice")
    new = "a-brand-new-password"
    resp = client.post("/profile", data={
        "current_password": "wrong", "password": new, "confirm": new,
    })
    assert resp.status_code == 200 and b"incorrect" in resp.data
    client.post("/profile", data={"current_password": PASSWORD, "password": new, "confirm": new})
    client.post("/logout")
    assert login(client, "alice", new).status_code == 302


def test_csrf_enforced_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("AISIGNX_SECRET_KEY", "test-secret")
    app = create_app({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'csrf.db').as_posix()}",
    })
    from flask_migrate import upgrade
    with app.app_context():
        upgrade()
    make_user(app, "admin", "admin")
    make_user(app, "alice", "viewer")
    client = app.test_client()

    assert login(client, "alice").status_code == 400  # no token

    token = csrf_token_from(client.get("/login").get_data(as_text=True))
    resp = client.post("/login", data={"username": "alice", "password": PASSWORD, "csrf_token": token})
    assert resp.status_code == 302
