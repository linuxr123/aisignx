"""Saved emergency messages (templates) and the API that sends them."""
import re

from aisignx.extensions import db
from aisignx.models import DisplayGroup, EmergencyMessage, EmergencyTemplate

from .test_api_tokens import make_token  # noqa: F401  (same helper the token tests use)
from .conftest import login, make_user


def _csrf(client, path="/emergency/"):
    page = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page).group(1)


def _template(app, **kw):
    with app.app_context():
        t = EmergencyTemplate(name=kw.get("name", "Fire drill"), title="Fire drill", message="Leave now",
                              level="critical", show_text=True, target=kw.get("target", "all"))
        db.session.add(t)
        db.session.commit()
        return t.id


def test_save_send_and_delete_from_the_page(admin_client, app):
    token = _csrf(admin_client)
    r = admin_client.post("/emergency/templates", data={
        "csrf_token": token, "title": "Fire drill", "message": "Leave now", "level": "critical",
        "target": "all", "playlist": "0", "show_text": "y", "template_name": "Fire drill"})
    assert r.status_code == 302
    with app.app_context():
        t = EmergencyTemplate.query.one()
        assert (t.name, t.target) == ("Fire drill", "all")
        tid = t.id
        assert EmergencyMessage.query.count() == 0           # saving sends nothing
    assert b"Fire drill" in admin_client.get("/emergency/").data
    admin_client.post(f"/emergency/templates/{tid}/send", data={"csrf_token": _csrf(admin_client)})
    with app.app_context():
        assert EmergencyMessage.query.filter_by(title="Fire drill").one().live
    admin_client.post(f"/emergency/templates/{tid}/delete", data={"csrf_token": _csrf(admin_client)})
    with app.app_context():
        assert EmergencyTemplate.query.count() == 0


def test_template_with_deleted_target_refuses_instead_of_widening(admin_client, app):
    with app.app_context():
        g = DisplayGroup(name="Lobby")
        db.session.add(g)
        db.session.commit()
        gid = g.id
    tid = _template(app, target=f"g:{gid}")
    with app.app_context():
        db.session.delete(db.session.get(DisplayGroup, gid))
        db.session.commit()
    admin_client.post(f"/emergency/templates/{tid}/send", data={"csrf_token": _csrf(admin_client)})
    with app.app_context():
        assert EmergencyMessage.query.count() == 0


def test_api_send_is_idempotent_and_clear(configured, client, app):
    uid = make_user(app, "panic", "editor")
    raw = make_token(app, uid, scopes="emergency:send")
    tid = _template(app)
    h = {"Authorization": f"Bearer {raw}"}
    r = client.post(f"/api/v1/emergency/templates/{tid}/send", headers=h)
    assert r.status_code == 201 and r.get_json()["already_live"] is False
    again = client.post(f"/api/v1/emergency/templates/{tid}/send", headers=h)
    assert again.status_code == 200 and again.get_json()["already_live"] is True
    with app.app_context():
        assert EmergencyMessage.query.count() == 1
    assert len(client.get("/api/v1/emergency", headers=h).get_json()["live"]) == 1
    cleared = client.post("/api/v1/emergency/clear", headers=h).get_json()["cleared"]
    assert len(cleared) == 1
    with app.app_context():
        assert EmergencyMessage.query.one().cleared_at is not None


def test_api_scope_and_role_are_enforced(configured, client, app):
    tid = _template(app)
    assert client.post(f"/api/v1/emergency/templates/{tid}/send").status_code == 401
    uid = make_user(app, "reader", "editor")
    raw = make_token(app, uid, scopes="media:read")
    h = {"Authorization": f"Bearer {raw}"}
    assert client.post(f"/api/v1/emergency/templates/{tid}/send", headers=h).status_code == 403
    assert client.post("/api/v1/emergency/templates/9999/send",
                       headers={"Authorization": f"Bearer {make_token(app, uid, scopes='emergency:send')}"}).status_code == 404
    vid = make_user(app, "viewer1", "viewer")
    vraw = make_token(app, vid, scopes="emergency:send")        # a viewer cannot hold the scope
    assert client.post(f"/api/v1/emergency/templates/{tid}/send",
                       headers={"Authorization": f"Bearer {vraw}"}).status_code == 403
