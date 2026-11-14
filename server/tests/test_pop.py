"""Proof of play: ingest (POST /api/player/v1/plays), rollup, reports, CSV and settings."""
import json
import shutil
import subprocess
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.models import AuditLog, Display, DisplayGroup, Media, PlayDaily, PlayEvent, Setting
from aisignx.pop import service
from aisignx.timeutil import iso_z, org_tz, to_local, utcnow

from .conftest import bearer, login, make_user

URL = "/api/player/v1/plays"
STATIC = Path(__file__).resolve().parents[1] / "aisignx" / "static" / "player"


def enable(app, tz=None, **settings):
    with app.app_context():
        Setting.set("pop.enabled", "1")
        if tz:
            Setting.set("timezone", tz)
        for key, value in settings.items():
            Setting.set(f"pop.{key}", value)
        db.session.commit()


def event(n=0, media_id=1, **kw):
    started = kw.pop("started", utcnow() - timedelta(minutes=5))
    data = {"id": f"evt-{n:08d}", "media_id": media_id, "item_id": 7,
            "started_at": iso_z(started), "duration_ms": 10_000}
    data.update(kw)
    return data


def post(client, token, events):
    return client.post(URL, json={"events": events}, headers=bearer(token))


def add_raw(app, display_id, media_id, started, ms=10_000, name="Clip", n=None):
    with app.app_context():
        count = db.session.query(PlayEvent).count()
        db.session.add(PlayEvent(display_id=display_id, media_id=media_id, media_name=name,
                                 client_event_id=f"raw-{n if n is not None else count:08d}",
                                 started_at=started, duration_ms=ms, received_at=utcnow()))
        db.session.commit()


def daily(app):
    with app.app_context():
        return sorted((r.day, r.display_id, r.media_id, r.plays, r.seconds, r.display_name,
                       r.media_name) for r in db.session.query(PlayDaily))


# --- ingest -----------------------------------------------------------------------------

def test_disabled_response(configured, client, paired_display):
    _id, token = paired_display
    resp = post(client, token, [event()])
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "disabled", "retry_after_s": 1800}
    with configured.app_context():
        assert db.session.query(PlayEvent).count() == 0


def test_requires_display_token(configured, client):
    enable(configured)
    resp = client.post(URL, json={"events": []})
    assert resp.status_code == 401 and resp.get_json()["error"] == "unauthorized"


def test_accepts_events_and_resend_is_duplicate(configured, client, paired_display):
    display_id, token = paired_display
    enable(configured)
    with configured.app_context():
        media = Media(name="Welcome", media_type="image")
        db.session.add(media)
        db.session.commit()
        media_id = media.id
    batch = [event(1, media_id=media_id), event(2, media_id=media_id, item_id=None),
             event(3, media_id=999)]
    resp = post(client, token, batch)
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "accepted": 3, "duplicate": 0, "dropped": 0}

    resp = post(client, token, batch)
    assert resp.get_json() == {"status": "ok", "accepted": 0, "duplicate": 3, "dropped": 0}
    with configured.app_context():
        rows = {e.client_event_id: e for e in db.session.query(PlayEvent)}
        assert len(rows) == 3
        assert rows["evt-00000001"].media_name == "Welcome"
        assert rows["evt-00000001"].display_id == display_id
        assert rows["evt-00000002"].item_id is None
        assert rows["evt-00000003"].media_name is None  # unknown media id: kept, no name


def test_repeat_inside_one_batch_is_a_duplicate(configured, client, paired_display):
    _id, token = paired_display
    enable(configured)
    resp = post(client, token, [event(1), event(1)])
    assert resp.get_json() == {"status": "ok", "accepted": 1, "duplicate": 1, "dropped": 0}


@pytest.mark.parametrize("bad", [
    {"duration_ms": "10000"},
    {"duration_ms": 10.5},
    {"duration_ms": 499},
    {"duration_ms": None},
    {"started_at": "yesterday"},
    {"started_at": "2026-11-12T10:00:00"},  # naive: not UTC
    {"started_at": 1731312000000},
    {"id": None},
    {"id": "short"},
    {"id": "has spaces in it"},
    {"media_id": 0},
    {"media_id": "1"},
    {"media_id": True},
    {"item_id": "7"},
])
def test_bad_event_is_dropped_never_an_error(configured, client, paired_display, bad):
    _id, token = paired_display
    enable(configured)
    resp = post(client, token, [event(1, **bad), event(2)])
    assert resp.status_code == 200
    assert resp.get_json() == {"status": "ok", "accepted": 1, "duplicate": 0, "dropped": 1}


def test_missing_keys_and_non_objects_are_dropped(configured, client, paired_display):
    _id, token = paired_display
    enable(configured)
    resp = post(client, token, [{}, "x", None, 5, {"id": "abcdefgh"}])
    assert resp.status_code == 200
    assert resp.get_json()["dropped"] == 5


def test_time_window_and_duration_clamp(configured, client, paired_display):
    _id, token = paired_display
    enable(configured)
    now = utcnow()
    resp = post(client, token, [
        event(1, started=now - timedelta(days=8)),          # too old
        event(2, started=now + timedelta(minutes=10)),      # too far ahead
        event(3, started=now + timedelta(minutes=2)),       # small clock skew is fine
        event(4, started=now - timedelta(days=6)),
        event(5, duration_ms=10**9),                        # clamped to a day
    ])
    assert resp.get_json() == {"status": "ok", "accepted": 3, "duplicate": 0, "dropped": 2}
    with configured.app_context():
        assert db.session.query(PlayEvent).filter_by(client_event_id="evt-00000005") \
            .one().duration_ms == 86_400_000


def test_events_older_than_raw_window_are_dropped(configured, client, paired_display):
    _id, token = paired_display
    enable(configured, raw_days="1")
    resp = post(client, token, [event(1, started=utcnow() - timedelta(days=3))])
    assert resp.get_json()["dropped"] == 1


def test_bad_bodies(configured, client, paired_display):
    _id, token = paired_display
    enable(configured)
    resp = client.post(URL, data="[1, 2]", content_type="application/json", headers=bearer(token))
    assert resp.status_code == 400 and resp.get_json()["error"] == "bad_request"
    resp = client.post(URL, json={"events": {"id": 1}}, headers=bearer(token))
    assert resp.status_code == 400
    resp = post(client, token, [event(i) for i in range(201)])
    assert resp.status_code == 400 and resp.get_json()["error"] == "too_many_events"
    # An empty POST is the player's probe after a "disabled" answer.
    resp = client.post(URL, headers=bearer(token))
    assert resp.get_json() == {"status": "ok", "accepted": 0, "duplicate": 0, "dropped": 0}


def test_body_limits(configured, client, paired_display):
    _id, token = paired_display
    enable(configured)
    # 200 ordinary events are well over the 8 KB default but inside the 64 KB limit.
    resp = post(client, token, [event(i) for i in range(200)])
    assert resp.status_code == 200 and resp.get_json()["accepted"] == 200
    resp = post(client, token, [event(i, pad="x" * 400) for i in range(200)])
    assert resp.status_code == 413
    assert resp.get_json()["error"] == "too_large"
    # Other endpoints keep the 8 KB limit.
    resp = client.post("/api/player/v1/heartbeat", json={"pad": "x" * 9000}, headers=bearer(token))
    assert resp.status_code == 413


# --- rollup -----------------------------------------------------------------------------

def test_rollup_is_idempotent_and_picks_up_late_events(configured, paired_display):
    display_id, _token = paired_display
    enable(configured)
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    add_raw(configured, display_id, 1, now - timedelta(hours=2), 10_000)
    add_raw(configured, display_id, 1, now - timedelta(hours=1), 5_500)
    add_raw(configured, display_id, 2, now - timedelta(days=1, hours=1), 20_000, name="Promo")
    with configured.app_context():
        service.rollup(now)
    first = daily(configured)
    assert first == [
        (date(2026, 11, 11), display_id, 2, 1, 20, "Lobby", "Promo"),
        (date(2026, 11, 12), display_id, 1, 2, 15, "Lobby", "Clip"),
    ]
    with configured.app_context():
        service.rollup(now + timedelta(minutes=5))
    assert daily(configured) == first

    # A late event for three days ago arrives after the watermark.
    later = now + timedelta(hours=1)
    with configured.app_context():
        db.session.add(PlayEvent(display_id=display_id, media_id=3, media_name="Late",
                                 client_event_id="late-0001", duration_ms=3000,
                                 started_at=now - timedelta(days=3), received_at=later))
        db.session.commit()
        service.rollup(later + timedelta(minutes=1))
    rows = daily(configured)
    assert (date(2026, 11, 9), display_id, 3, 1, 3, "Lobby", "Late") in rows
    assert len(rows) == 3


def test_rollup_local_day_boundaries(configured, paired_display):
    display_id, _token = paired_display
    enable(configured, tz="America/New_York")  # UTC-5 in November
    now = datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc)
    # 03:00Z on the 12th is 22:00 on the 11th in New York; 05:30Z is 00:30 on the 12th.
    add_raw(configured, display_id, 1, datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc))
    add_raw(configured, display_id, 1, datetime(2026, 11, 12, 5, 30, tzinfo=timezone.utc))
    add_raw(configured, display_id, 1, datetime(2026, 11, 12, 4, 59, tzinfo=timezone.utc))
    with configured.app_context():
        service.rollup(now)
    assert [(r[0], r[3]) for r in daily(configured)] == [
        (date(2026, 11, 11), 2), (date(2026, 11, 12), 1)]


def test_rollup_names_deleted_display(configured, paired_display):
    display_id, _token = paired_display
    enable(configured)
    now = utcnow()
    add_raw(configured, display_id, 1, now - timedelta(minutes=30))
    with configured.app_context():
        # Without the FK cascade (e.g. a restored backup) the display may be gone.
        db.session.execute(db.text("PRAGMA foreign_keys=OFF"))
        db.session.execute(db.text("DELETE FROM displays"))
        db.session.commit()
        service.rollup(now)
    [row] = daily(configured)
    assert row[5] == f"Display #{display_id}"


def test_deleting_a_display_keeps_its_plays_in_the_daily_rows(configured, client,
                                                              paired_display):
    display_id, _token = paired_display
    enable(configured)
    with configured.app_context():
        other = Display(name="Window")
        db.session.add(other)
        db.session.commit()
        other_id = other.id
    now = utcnow()
    add_raw(configured, display_id, 1, now - timedelta(days=1))
    add_raw(configured, other_id, 1, now - timedelta(days=1))
    with configured.app_context():
        service.rollup(now)
    add_raw(configured, display_id, 1, now - timedelta(minutes=5))  # not rolled up yet
    login(client, "admin")
    client.post(f"/displays/{display_id}/delete")
    with configured.app_context():
        assert db.session.get(Display, display_id) is None
        assert PlayEvent.query.filter_by(display_id=display_id).count() == 0  # cascade
    before = [r for r in daily(configured) if r[1] == display_id]
    assert sum(r[3] for r in before) == 2  # yesterday's play and the one just before delete

    # The hourly rollup rebuilds yesterday and today; a late event from another screen
    # makes it rebuild an older day too. The deleted display's rows survive all of it.
    later = utcnow() + timedelta(hours=1)
    with configured.app_context():
        db.session.add(PlayEvent(display_id=other_id, media_id=1, media_name="Clip",
                                 client_event_id="late-0001", duration_ms=1000,
                                 started_at=now - timedelta(days=3), received_at=later))
        db.session.commit()
        service.rollup(later + timedelta(minutes=1))
    assert [r for r in daily(configured) if r[1] == display_id] == before
    assert sum(r[3] for r in daily(configured) if r[1] == other_id) == 2


def test_retention_purge(configured, paired_display):
    display_id, _token = paired_display
    enable(configured, raw_days="2", daily_days="30")
    now = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)
    add_raw(configured, display_id, 1, now - timedelta(days=5))
    add_raw(configured, display_id, 1, now - timedelta(hours=3))
    with configured.app_context():
        for day in (date(2026, 9, 1), date(2026, 10, 20)):
            db.session.add(PlayDaily(day=day, display_id=display_id, media_id=1, plays=1, seconds=1))
        db.session.commit()
        result = service.rollup(now)
        assert "purged 1 raw and 1 daily" in result
        assert db.session.query(PlayEvent).count() == 1
        days = {r.day for r in db.session.query(PlayDaily)}
    assert date(2026, 9, 1) not in days and date(2026, 10, 20) in days
    # The day with the purged raw event is outside the raw window, so it is not rebuilt.
    assert date(2026, 11, 7) not in days


def test_rollup_job_runs_through_jobs(configured):
    from .conftest import run_job

    assert run_job(configured, "pop.rollup").startswith("rebuilt")
    with configured.app_context():
        assert Setting.get_datetime(service.WATERMARK) is not None


# --- reports ----------------------------------------------------------------------------

@pytest.fixture
def report_data(configured):
    """Two screens (one in a group), two media, three days of daily rows."""
    with configured.app_context():
        group = DisplayGroup(name="Shop")
        db.session.add(group)
        db.session.flush()
        a = Display(name="Lobby")
        b = Display(name="Window", group_id=group.id)
        db.session.add_all([a, b])
        db.session.commit()
        today = to_local(utcnow(), org_tz()).date()  # the report's idea of today
        rows = [
            (today, a.id, 1, "Clip", 10, 100), (today, b.id, 1, "Clip", 5, 50),
            (today - timedelta(days=1), a.id, 2, "=Promo", 3, 30),
            (today - timedelta(days=20), a.id, 1, "Clip", 99, 990),  # outside 7 days
        ]
        for day, did, mid, mname, plays, secs in rows:
            db.session.add(PlayDaily(day=day, display_id=did, media_id=mid, media_name=mname,
                                     display_name="Lobby" if did == a.id else "Window",
                                     plays=plays, seconds=secs))
        db.session.commit()
        return {"a": a.id, "b": b.id, "group": group.id, "today": today}


def test_report_totals(configured, report_data):
    with configured.app_context():
        today = report_data["today"]
        start = today - timedelta(days=6)
        data = service.report(start, today, "media")
        assert data["total"] == {"plays": 18, "seconds": 180, "screens": 2}
        assert [(r["key"], r["plays"], r["screens"]) for r in data["rows"]] == [(1, 15, 2), (2, 3, 1)]
        data = service.report(start, today, "display", display_ids=[report_data["b"]])
        assert [(r["label"], r["plays"]) for r in data["rows"]] == [("Window", 5)]
        data = service.report(start, today, "day", media_id=1)
        assert [(r["label"], r["plays"]) for r in data["rows"]] == [(today.isoformat(), 15)]
        assert service.report(start, today, "media", display_ids=[])["total"]["plays"] == 0


def test_report_page_for_viewer(configured, client, report_data, monkeypatch):
    make_user(configured, "vera", "viewer")
    login(client, "vera")
    resp = client.get("/reports/plays")
    html = resp.get_data(as_text=True)
    assert resp.status_code == 200
    assert "data-pop-off" in html  # off by default
    assert 'id="pop-settings"' not in html  # settings are admin only
    assert "data-pop-total-plays>18<" in html
    resp = client.get(f"/reports/plays?target=g{report_data['group']}&group=display")
    html = resp.get_data(as_text=True)
    assert "data-pop-total-plays>5<" in html and "Window" in html
    # Bad filter values fall back instead of failing.
    assert client.get("/reports/plays?start=nope&end=2026-13-40&group=x&media=-3").status_code == 200
    assert client.post("/reports/settings", data={"enabled": "y", "raw_days": 5,
                                                  "daily_days": 100}).status_code == 403


def test_daily_csv(configured, client, report_data):
    make_user(configured, "vera", "viewer")
    login(client, "vera")
    resp = client.get("/reports/plays.csv")
    assert resp.status_code == 200 and resp.mimetype == "text/csv"
    text = resp.get_data(as_text=True).lstrip("﻿")
    lines = text.strip().splitlines()
    assert lines[0] == "day,display_id,display,media_id,media,plays,seconds"
    assert len(lines) == 4
    assert ",'=Promo,3,30" in text  # formula-safe
    resp = client.get(f"/reports/plays.csv?target=d{report_data['b']}")
    assert len(resp.get_data(as_text=True).strip().splitlines()) == 2


def test_raw_csv(configured, client, paired_display):
    display_id, token = paired_display
    enable(configured)
    login(client, "admin")
    post(client, token, [event(1, media_id=4), event(2, media_id=4, duration_ms=1500)])
    resp = client.get("/reports/plays-raw.csv")
    assert resp.status_code == 200
    lines = resp.get_data(as_text=True).lstrip("﻿").strip().splitlines()
    assert lines[0].startswith("started_at_utc,started_local,display_id,display,media_id")
    assert len(lines) == 3 and ",Lobby,4," in lines[1] and ",1.5," in lines[2]

    old = (utcnow().date() - timedelta(days=30)).isoformat()
    resp = client.get(f"/reports/plays-raw.csv?start={old}")
    assert resp.status_code == 302 and "/reports/plays" in resp.headers["Location"]
    page = client.get(resp.headers["Location"]).get_data(as_text=True)
    assert "Individual plays are kept for 14 days" in page


def test_settings_admin_only_and_audited(configured, client):
    make_user(configured, "ed", "editor")
    login(client, "ed")
    assert client.post("/reports/settings", data={"raw_days": 5, "daily_days": 100}).status_code == 403
    client.post("/logout")

    login(client, "admin")
    html = client.get("/reports/plays").get_data(as_text=True)
    assert 'id="pop-settings"' in html
    resp = client.post("/reports/settings", data={"enabled": "y", "raw_days": 5, "daily_days": 100})
    assert resp.status_code == 302
    with configured.app_context():
        assert Setting.get_bool("pop.enabled")
        assert Setting.get_int("pop.raw_days") == 5
        row = db.session.query(AuditLog).filter_by(action="pop.settings").one()
        assert row.detail_data["changed"]["pop.enabled"] == {"from": "0", "to": "1"}

    resp = client.post("/reports/settings", data={"raw_days": 91, "daily_days": 100},
                       follow_redirects=True)
    assert "were not saved" in resp.get_data(as_text=True)
    with configured.app_context():
        assert Setting.get_int("pop.raw_days") == 5 and Setting.get_bool("pop.enabled")


# --- player -----------------------------------------------------------------------------

def test_player_page_loads_pop_before_player(configured, client):
    html = client.get("/player/").get_data(as_text=True)
    assert html.index("player/pop.js") < html.index("player/player.js")
    resp = client.get("/static/player/pop.js")
    assert resp.status_code == 200 and "AiSignXPop" in resp.get_data(as_text=True)
    resp.close()


def test_player_calls_pop_hooks():
    player = (STATIC / "player.js").read_text(encoding="utf-8")
    assert "window.AiSignXPop" in player
    assert 'pop("end", true); pop("begin", item);' in player
    assert 'pop("end", false);' in player


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_pop_js_queue():
    script = Path(__file__).resolve().parent / "js" / "pop_queue.js"
    out = json.loads(subprocess.run(["node", str(script), str(STATIC / "pop.js")],
                                    capture_output=True, text=True, timeout=30,
                                    check=True).stdout)
    basic = out["basic"]
    assert [(e["item_id"], e["media_id"], e["duration_ms"]) for e in basic] == [
        (5, 9, 10000), (None, 3, 2000)]  # short, failed and media-less plays are skipped
    assert basic[0]["started_at"].endswith("Z") and 8 <= len(basic[0]["id"]) <= 64
    assert out["covered"] == [3000, 4000]  # fullscreen emergency subtracted, banner not
    assert (out["afterError"], out["afterOk"], out["sentCounts"]) == (3, 0, [3, 3])
    assert (out["disabledQueued"], out["whileOff"], out["callsWhileOff"]) == (0, 0, 1)
    assert out["probe"] == 0 and out["afterProbe"] == 1
    assert out["cap"] == {"queued": 1000, "firstItem": 5, "calls": 1, "batch": 50}
    assert out["pagehide"] == [2500]
