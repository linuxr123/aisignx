from aisignx.extensions import db
from aisignx.models import Setting, User

from .conftest import PASSWORD, make_user


def _setup_data(**overrides):
    data = {
        "org_name": "Acme Signs",
        "timezone": "Europe/Brussels",
        "username": "boss",
        "email": "",
        "password": PASSWORD,
        "confirm": PASSWORD,
    }
    data.update(overrides)
    return data


def test_fresh_install_redirects_everything_to_setup(client):
    for path in ("/", "/login", "/dashboard", "/users/"):
        resp = client.get(path)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/setup/")


def test_healthz_available_during_setup(client):
    assert client.get("/healthz").json == {"status": "ok"}


def test_wizard_creates_admin_and_settings(app, client):
    assert client.get("/setup/").status_code == 200
    resp = client.post("/setup/", data=_setup_data())
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/dashboard")

    with app.app_context():
        admin = User.query.filter_by(username="boss").one()
        assert admin.role == "admin"
        assert admin.check_password(PASSWORD)
        assert Setting.get("org_name") == "Acme Signs"
        assert Setting.get("timezone") == "Europe/Brussels"

    # Logged in straight away.
    assert client.get("/dashboard").status_code == 200


def test_wizard_rejects_short_password_and_mismatch(app, client):
    client.post("/setup/", data=_setup_data(password="short", confirm="short"))
    client.post("/setup/", data=_setup_data(confirm="something-else-entirely"))
    with app.app_context():
        assert db.session.query(User).count() == 0


def test_wizard_gone_after_setup(app, client):
    make_user(app, "admin", "admin")
    assert client.get("/setup/").status_code == 404
    assert client.post("/setup/", data=_setup_data(username="intruder")).status_code == 404
    with app.app_context():
        assert User.query.filter_by(username="intruder").first() is None
