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


def test_plans_fire_expire_and_skip_stale_ones(admin_client, app):
    from datetime import timedelta
    from aisignx.emergency import plans
    from aisignx.models import EmergencyPlan
    from aisignx.timeutil import utcnow
    tid = _template(app)
    now = utcnow()
    with app.app_context():
        db.session.add_all([
            EmergencyPlan(template_id=tid, starts_at=now - timedelta(seconds=5), duration_min=30),   # due now
            EmergencyPlan(template_id=tid, starts_at=now - timedelta(hours=2)),                       # stale
            EmergencyPlan(template_id=tid, starts_at=now + timedelta(hours=1))])                      # later
        db.session.commit()
        assert plans.fire_due(now) == 1
        states = sorted(p.state for p in EmergencyPlan.query.all())
        assert states == ["fired", "missed", "pending"]
        msg = EmergencyMessage.query.one()
        assert msg.live and msg.ends_at is not None
        assert plans.expire_due(now) == 0                                   # not yet
        assert plans.expire_due(now + timedelta(minutes=31)) == 1
        db.session.refresh(msg)
        assert not msg.live and msg.clear_reason == "expired"
        assert plans.fire_due(now) == 0                                      # nothing fires twice


def test_plan_with_a_gone_target_fails_instead_of_widening(admin_client, app):
    from datetime import timedelta
    from aisignx.emergency import plans
    from aisignx.models import EmergencyPlan
    from aisignx.timeutil import utcnow
    tid = _template(app, target="g:424242")
    with app.app_context():
        db.session.add(EmergencyPlan(template_id=tid, starts_at=utcnow()))
        db.session.commit()
        assert plans.fire_due() == 0
        assert EmergencyPlan.query.one().state == "failed" and EmergencyMessage.query.count() == 0


def test_schedule_page_flow(admin_client, app):
    tid = _template(app)
    token = _csrf(admin_client)
    bad = admin_client.post("/emergency/plans", data={"csrf_token": token, "template_id": tid,
                                                      "starts_at": "2001-01-01T10:00"})
    assert bad.status_code == 302
    from aisignx.models import EmergencyPlan
    with app.app_context():
        assert EmergencyPlan.query.count() == 0                        # in the past: refused
    ok = admin_client.post("/emergency/plans", data={"csrf_token": token, "template_id": tid,
                                                     "starts_at": "2099-01-01T10:00", "duration_min": "15"})
    assert ok.status_code == 302
    with app.app_context():
        p = EmergencyPlan.query.one()
        assert p.state == "pending" and p.duration_min == 15
        pid = p.id
    assert b"Scheduled" in admin_client.get("/emergency/").data
    admin_client.post(f"/emergency/plans/{pid}/cancel", data={"csrf_token": _csrf(admin_client)})
    with app.app_context():
        assert EmergencyPlan.query.one().state == "cancelled"


def test_a_repeating_plan_is_followed_by_its_next_occurrence_at_the_same_local_time(admin_client, app):
    from datetime import datetime, timedelta, timezone
    from aisignx.emergency import plans
    from aisignx.models import EmergencyPlan, Setting
    from aisignx.timeutil import org_tz
    tid = _template(app)
    with app.app_context():
        Setting.set("timezone", "America/New_York")
        tz = org_tz()
        fri = datetime(2026, 3, 6, 10, 0, tzinfo=tz)                       # Friday 10:00 local, a week before the clock change
        utc = fri.astimezone(timezone.utc)
        assert plans.next_start(utc, "none", utc) is None
        wk = plans.next_start(utc, "weekdays", utc).astimezone(tz)
        assert (wk.weekday(), wk.hour, wk.day) == (0, 10, 9)               # Friday -> Monday, still 10:00
        dl = plans.next_start(fri.replace(day=7).astimezone(timezone.utc), "daily", utc).astimezone(tz)
        assert (dl.day, dl.hour) == (8, 10)
        w = plans.next_start(fri.replace(day=6).astimezone(timezone.utc), "weekly", utc).astimezone(tz)
        assert (w.month, w.day, w.hour) == (3, 13, 10)
        dst = plans.next_start(datetime(2026, 3, 7, 10, 0, tzinfo=tz).astimezone(timezone.utc), "daily", utc).astimezone(tz)
        assert dst.day == 8 and dst.hour == 10                              # the clocks changed on the 8th: still 10:00
        # firing a repeating plan leaves the next one pending; cancelling the pending one ends the series
        now = datetime.now(timezone.utc)
        db.session.add(EmergencyPlan(template_id=tid, starts_at=now - timedelta(seconds=5), repeat="daily"))
        db.session.commit()
        assert plans.fire_due(now) == 1
        rows = sorted(EmergencyPlan.query.all(), key=lambda p: p.id)
        assert [p.state for p in rows] == ["fired", "pending"] and rows[1].repeat == "daily"
        assert 23 <= (rows[1].starts_at.replace(tzinfo=timezone.utc) - now).total_seconds() / 3600 <= 25
        assert plans.fire_due(now) == 0                                      # one occurrence only


def test_each_saved_message_has_its_own_schedule_form_and_repeat_is_saved(admin_client, app):
    tid = _template(app)
    page = admin_client.get("/emergency/").get_data(as_text=True)
    assert f'name="template_id" value="{tid}"' in page and 'name="repeat"' in page
    r = admin_client.post("/emergency/plans", data={"csrf_token": _csrf(admin_client), "template_id": tid,
                                                    "starts_at": "2099-01-05T10:00", "repeat": "weekdays", "duration_min": "30"})
    assert r.status_code == 302
    from aisignx.models import EmergencyPlan
    with app.app_context():
        p = EmergencyPlan.query.one()
        assert p.repeat == "weekdays" and p.duration_min == 30
    assert b"weekdays" in admin_client.get("/emergency/").data


# ---- planning through the API ----------------------------------------------------------------------------------------------
def _api(app):
    uid = make_user(app, "planner", "editor")
    return {"Authorization": f"Bearer {make_token(app, uid, scopes='emergency:send')}"}


def test_the_api_plans_lists_and_cancels_a_saved_message(configured, client, app):
    from datetime import datetime, timedelta, timezone
    from aisignx.models import EmergencyPlan
    h = _api(app)
    tid = _template(app)
    when = (datetime.now(timezone.utc) + timedelta(days=2)).replace(microsecond=0)
    body = {"template_id": tid, "starts_at": when.isoformat().replace("+00:00", "Z"), "duration_min": 30, "repeat": "weekdays"}
    r = client.post("/api/v1/emergency/plans", json=body, headers=h)
    assert r.status_code == 201 and r.get_json()["already_planned"] is False
    plan = r.get_json()["plan"]
    assert plan["template_id"] == tid and plan["duration_min"] == 30 and plan["repeat"] == "weekdays" and plan["state"] == "pending"
    again = client.post("/api/v1/emergency/plans", json=body, headers=h)                       # a retried webhook
    assert again.status_code == 200 and again.get_json()["already_planned"] is True and again.get_json()["plan"]["id"] == plan["id"]
    listing = client.get("/api/v1/emergency/plans", headers=h).get_json()
    assert [p["id"] for p in listing["pending"]] == [plan["id"]]
    cancelled = client.delete(f"/api/v1/emergency/plans/{plan['id']}", headers=h)
    assert cancelled.status_code == 200 and cancelled.get_json()["plan"]["state"] == "cancelled"
    assert client.delete(f"/api/v1/emergency/plans/{plan['id']}", headers=h).status_code == 409        # already cancelled
    assert client.get("/api/v1/emergency/plans", headers=h).get_json()["pending"] == []
    with app.app_context():
        assert EmergencyPlan.query.count() == 1


def test_a_time_without_an_offset_is_the_organizations_local_time(configured, client, app):
    from aisignx.models import EmergencyPlan, Setting
    h = _api(app)
    tid = _template(app)
    with app.app_context():
        Setting.set("timezone", "America/New_York")
        db.session.commit()
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    day = (datetime.now(timezone.utc) + timedelta(days=60)).date()
    r = client.post("/api/v1/emergency/plans", json={"template_id": tid, "starts_at": f"{day}T10:00"}, headers=h)
    assert r.status_code == 201
    expected = datetime(day.year, day.month, day.day, 10, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc)
    assert r.get_json()["plan"]["starts_at"].startswith(expected.strftime("%Y-%m-%dT%H:%M:00"))   # 10:00 in New York, whatever the clock rule
    with app.app_context():
        assert EmergencyPlan.query.one().duration_min is None and EmergencyPlan.query.one().repeat == "none"


def test_the_plan_api_refuses_bad_input_and_needs_the_scope(configured, client, app):
    h = _api(app)
    tid = _template(app)
    from datetime import datetime, timedelta, timezone
    good = {"template_id": tid, "starts_at": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()}
    for bad, code in (({"starts_at": good["starts_at"]}, 404), ({**good, "template_id": 9999}, 404), ({**good, "starts_at": "tomorrow"}, 400),
                      ({**good, "starts_at": "2000-01-01T00:00:00Z"}, 400), ({**good, "starts_at": "2200-01-01T00:00:00Z"}, 400),
                      ({**good, "duration_min": 0}, 400), ({**good, "duration_min": 5000}, 400), ({**good, "duration_min": "30"}, 400),
                      ({**good, "duration_min": True}, 400), ({**good, "repeat": "hourly"}, 400)):
        assert client.post("/api/v1/emergency/plans", json=bad, headers=h).status_code == code, bad
    assert client.post("/api/v1/emergency/plans", data="not json", headers=h).status_code == 400
    assert client.post("/api/v1/emergency/plans", json=good).status_code == 401                 # no token
    vid = make_user(app, "viewer", "viewer")
    vraw = make_token(app, vid, scopes="emergency:send")
    assert client.post("/api/v1/emergency/plans", json=good, headers={"Authorization": f"Bearer {vraw}"}).status_code == 403
    assert client.delete("/api/v1/emergency/plans/1", headers={"Authorization": f"Bearer {vraw}"}).status_code == 403
    assert client.delete("/api/v1/emergency/plans/12345", headers=h).status_code == 404


def test_the_plan_api_fires_like_any_other_plan(configured, client, app):
    from datetime import datetime, timedelta, timezone
    from aisignx.emergency import plans
    h = _api(app)
    tid = _template(app)
    soon = datetime.now(timezone.utc) + timedelta(seconds=30)
    r = client.post("/api/v1/emergency/plans", json={"template_id": tid, "starts_at": soon.isoformat(), "duration_min": 5, "repeat": "daily"}, headers=h)
    assert r.status_code == 201
    with app.app_context():
        assert plans.fire_due(soon + timedelta(seconds=1)) == 1
        msg = EmergencyMessage.query.one()
        assert msg.live and msg.ends_at is not None
    again = client.get("/api/v1/emergency/plans", headers=h).get_json()
    assert [p["repeat"] for p in again["pending"]] == ["daily"] and again["recent"][0]["state"] == "fired"       # the next occurrence is waiting


def test_the_pending_plans_are_capped(configured, client, app):
    from aisignx.api import emergency as api_emergency
    from aisignx.models import EmergencyPlan
    from aisignx.timeutil import utcnow
    from datetime import timedelta
    h = _api(app)
    tid = _template(app)
    with app.app_context():
        for i in range(api_emergency.MAX_PENDING_PLANS):
            db.session.add(EmergencyPlan(template_id=tid, starts_at=utcnow() + timedelta(days=1, minutes=i)))
        db.session.commit()
    r = client.post("/api/v1/emergency/plans", json={"template_id": tid, "starts_at": (utcnow() + timedelta(days=30)).isoformat()}, headers=h)
    assert r.status_code == 409
