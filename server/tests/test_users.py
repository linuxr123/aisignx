import pytest

from aisignx.extensions import db
from aisignx.models import User

from .conftest import PASSWORD, login, make_user


def _user(app, username):
    with app.app_context():
        return User.query.filter_by(username=username).first()


@pytest.mark.parametrize("role", ["editor", "viewer"])
def test_non_admins_cannot_manage_users_or_settings(app, client, role):
    make_user(app, "admin", "admin")
    make_user(app, "someone", role)
    login(client, "someone")
    assert client.get("/users/").status_code == 403
    assert client.get("/users/new").status_code == 403
    assert client.get("/settings/").status_code == 403
    assert client.get("/dashboard").status_code == 200


def test_anonymous_redirected_to_login(app, client):
    make_user(app, "admin", "admin")
    resp = client.get("/users/")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_admin_creates_edits_and_deletes_user(app, admin_client):
    assert admin_client.get("/users/").status_code == 200
    resp = admin_client.post("/users/new", data={
        "username": "carol", "email": "carol@example.com", "role": "editor", "password": PASSWORD,
    })
    assert resp.status_code == 302
    carol = _user(app, "carol")
    assert carol.role == "editor"

    admin_client.post(f"/users/{carol.id}/edit", data={"email": "", "role": "viewer", "active": "y"})
    carol = _user(app, "carol")
    assert carol.role == "viewer" and carol.email is None

    admin_client.post(f"/users/{carol.id}/delete")
    assert _user(app, "carol") is None


def test_duplicate_username_rejected_case_insensitive(app, admin_client):
    resp = admin_client.post("/users/new", data={"username": "ADMIN", "role": "viewer", "password": PASSWORD})
    assert resp.status_code == 200 and b"already taken" in resp.data


def test_admin_cannot_demote_or_delete_self(app, admin_client):
    me = _user(app, "admin")
    admin_client.post(f"/users/{me.id}/edit", data={"role": "viewer", "active": "y"})
    admin_client.post(f"/users/{me.id}/delete")
    me = _user(app, "admin")
    assert me is not None and me.role == "admin"


def test_admin_can_manage_other_admins(app, admin_client):
    other_id = make_user(app, "admin2", "admin", active=False)
    admin_client.post(f"/users/{other_id}/edit", data={"role": "admin", "active": "y"})
    assert _user(app, "admin2").is_active
    # Now two active admins: demoting the other one is allowed.
    admin_client.post(f"/users/{other_id}/edit", data={"role": "editor", "active": "y"})
    assert _user(app, "admin2").role == "editor"


def test_settings_page_saves(app, admin_client):
    assert admin_client.get("/settings/").status_code == 200
    admin_client.post("/settings/", data={"org_name": "New Name", "timezone": "America/New_York"})
    from aisignx.models import Setting
    with app.app_context():
        assert Setting.get("org_name") == "New Name"
        assert Setting.get("timezone") == "America/New_York"
    assert b"New Name" in admin_client.get("/dashboard").data


def test_settings_rejects_unknown_timezone(app, admin_client):
    resp = admin_client.post("/settings/", data={"org_name": "X", "timezone": "Mars/Base"})
    assert resp.status_code == 200
    from aisignx.models import Setting
    with app.app_context():
        assert Setting.get("timezone") == "UTC"
        assert db.session.get(Setting, "org_name") is None
