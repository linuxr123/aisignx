"""Heartbeat diagnostics: whitelist, size cap, storage and the detail page card."""
import json

import pytest
from sqlalchemy import update

from aisignx.extensions import db
from aisignx.models import Display
from aisignx.player import diag

from .conftest import DISPLAY_TOKEN, bearer
from .test_player_stream import _drain, listener  # noqa: F401  (fixture)

API = "/api/player/v1"

FULL = {
    "ua": "Mozilla/5.0 " + "x" * 400,
    "build": "3.0.0-abcdef12",
    "page_uptime_s": 125.7,
    "screen": {"w": 1920, "h": 1080, "dpr": 1.5, "extra": 1},
    "viewport": {"w": 1920, "h": 1080},
    "shell": {"name": "electron", "version": "3.0.0", "uptime_s": 90061,
              "caps": ["capture", "pin-exit", 7, "y" * 40] + ["c"] * 10, "secret": "no"},
    "cache": {"files": 12, "bytes": 123456789},
    "storage_free_bytes": 2**60,
    "errors": [{"t": 1_700_000_000_000 + i, "msg": f"<script>e{i}</script>" + "m" * 300}
               for i in range(14)],
    "token": "must-not-be-stored",
}


def _beat(client, payload):
    return client.post(f"{API}/heartbeat", headers=bearer(DISPLAY_TOKEN), json=payload)


def test_clean_whitelists_and_clamps():
    data = json.loads(diag.clean(FULL))
    assert "token" not in data and "secret" not in data["shell"]
    assert len(data["ua"]) == 300 and data["page_uptime_s"] == 125
    assert data["screen"] == {"w": 1920, "h": 1080, "dpr": 1.5}
    caps = data["shell"]["caps"]
    assert len(caps) == 8 and caps[:2] == ["capture", "pin-exit"] and len(caps[2]) == 20
    assert data["storage_free_bytes"] == 2**53
    assert len(data["errors"]) == 10 and data["errors"][-1]["t"] == 1_700_000_000_013
    assert all(len(e["msg"]) == 200 for e in data["errors"])


@pytest.mark.parametrize("value", [None, [], "text", 5])
def test_clean_rejects_non_objects(value):
    assert diag.clean(value) is None


def test_clean_drops_oversized():
    assert diag.clean({"errors": [{"t": 1, "msg": '"' * 200}] * 10, "ua": '"' * 300}) is None
    assert diag.clean({"page_uptime_s": float("inf"), "screen": {"w": -5, "dpr": "x"}}) == \
        '{"page_uptime_s":9007199254740992,"screen":{"w":0}}'


def test_heartbeat_stores_diag_without_publishing(app, configured, client, paired_display,
                                                  listener):  # noqa: F811
    display_id = paired_display[0]
    _drain(listener)
    assert _beat(client, {"client_id": "abc", "diag": FULL}).status_code == 200
    assert _drain(listener) == []
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display.diagnostics_at is not None
        stored = json.loads(display.diagnostics)
        assert stored["shell"]["name"] == "electron" and "token" not in stored
        first_at = display.diagnostics_at

    # A beat without diag keeps the last report.
    assert _beat(client, {"client_id": "abc"}).status_code == 200
    with app.app_context():
        assert db.session.get(Display, display_id).diagnostics_at == first_at


def test_oversized_or_bad_diag_dropped_but_heartbeat_ok(app, configured, client, paired_display):
    display_id = paired_display[0]
    big = {"ua": '"' * 300, "errors": [{"t": 1, "msg": '"' * 200}] * 10}
    assert len(json.dumps({"diag": big})) < 8192
    for value in (big, "not an object", [1, 2]):
        resp = _beat(client, {"client_id": "abc", "diag": value})
        assert resp.status_code == 200
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display.diagnostics is None and display.last_seen_at is not None


def _set_diag(app, display_id, text):
    with app.app_context():
        db.session.execute(update(Display).where(Display.id == display_id)
                           .values(diagnostics=text, diagnostics_at=db.func.now()))
        db.session.commit()


def test_detail_page_diagnostics_card(app, admin_client, paired_display):
    display_id = paired_display[0]
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert "No diagnostics reported yet." in page

    _set_diag(app, display_id, diag.clean(FULL))
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert "1920 &times; 1080 @ 1.5x" in page
    assert "electron 3.0.0" in page and "capture, pin-exit" in page
    assert "1 d 1 h" in page and "2 min" in page
    assert "&lt;script&gt;e13&lt;/script&gt;" in page and "<script>e13" not in page

    _set_diag(app, display_id, "{corrupt")
    resp = admin_client.get(f"/displays/{display_id}")
    assert resp.status_code == 200
    assert "could not be read" in resp.get_data(as_text=True)

    _set_diag(app, display_id, json.dumps({"screen": "x", "errors": 5, "shell": [1]}))
    assert admin_client.get(f"/displays/{display_id}").status_code == 200

    # Partial objects pass the whitelist (missing keys are just left out).
    partial = {"cache": {"files": 3}, "shell": {"name": "android"}, "errors": [{"msg": "x"}]}
    _set_diag(app, display_id, diag.clean(partial))
    resp = admin_client.get(f"/displays/{display_id}")
    assert resp.status_code == 200 and "3 files" in resp.get_data(as_text=True)


def test_status_json_new_fields(app, admin_client, paired_display):
    _beat(admin_client, {"client_id": "abc", "diag": {"build": "b"}})
    entry = admin_client.get("/displays/status.json").get_json()["displays"][0]
    assert {"screenshot_at", "screenshot_pending", "diagnostics_at"} <= set(entry)
    assert entry["screenshot_at"] is None and entry["screenshot_pending"] is False
    assert entry["diagnostics_at"].endswith("Z")


def test_clean_drops_unrenderable_error_times():
    # Year 9999 overflows when shifted to a zone ahead of UTC (Linux), so it never gets stored.
    data = json.loads(diag.clean({"errors": [{"t": 253_402_300_799_000, "msg": "late"},
                                             {"t": diag.MAX_TIME_MS, "msg": "edge"}]}))
    assert [e["t"] for e in data["errors"]] == [0, diag.MAX_TIME_MS]


def test_ms_time_survives_overflow_in_zone_conversion(app, monkeypatch):
    from aisignx import filters

    def boom(*_args, **_kwargs):
        raise OverflowError("date value out of range")

    monkeypatch.setattr(filters, "localtime", boom)
    assert filters.ms_time(253_402_300_799_000) == "—"


def test_detail_page_survives_far_future_error_time(app, admin_client, paired_display):
    with app.app_context():
        from aisignx.models import Setting
        Setting.set("timezone", "Europe/Berlin")
        db.session.commit()
    display_id = paired_display[0]
    # Stored raw (as a pre-fix server could have), bypassing clean().
    _set_diag(app, display_id, json.dumps({"errors": [{"t": 253_402_300_799_000, "msg": "x"},
                                                      {"t": 2**53, "msg": "y"}]}))
    assert admin_client.get(f"/displays/{display_id}").status_code == 200


# --- Video support reported by the player (media_support) --------------------------------

def test_clean_keeps_only_valid_media_support():
    data = json.loads(diag.clean({"media_support": {
        "checked": 5.9, "extra": 1,
        "problems": [{"id": 3, "s": "no", "x": 1}, {"id": 4, "s": "rough"}, {"id": 5, "s": "bad"},
                     {"id": "7", "s": "no"}, "text", {"id": 0, "s": "no"}] + [{"id": 9, "s": "no"}] * 40}}))
    assert data["media_support"]["checked"] == 5
    assert data["media_support"]["problems"][:2] == [{"id": 3, "s": "no"}, {"id": 4, "s": "rough"}]
    assert len(data["media_support"]["problems"]) <= 20
    assert diag.clean({"media_support": {"problems": []}}) == "{}"  # no "checked": dropped
    assert diag.clean({"media_support": "x"}) == "{}"


def test_displays_pages_show_what_the_player_cannot_play(app, admin_client, paired_display):
    from aisignx.models import Media
    display_id = paired_display[0]
    with app.app_context():
        big = Media(name="Lobby film", media_type="video", width=3840, height=2160,
                    video_info={"codec": "h264", "profile": "High", "level": 51, "fps": 25.0, "bitrate": 40_000_000})
        db.session.add(big)
        db.session.commit()
        big_id = big.id
    _set_diag(app, display_id, json.dumps({"media_support": {"checked": 2, "problems": [
        {"id": big_id, "s": "no"}, {"id": 9999, "s": "rough"}]}}))
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert "Playback warnings" in page and "Lobby film" in page and "this screen cannot decode it" in page
    assert "3840×2160" in page and "H264 High" in page and "(deleted)" in page and "Make TV-friendly copy" in page
    assert "May stutter" in admin_client.get("/displays/").get_data(as_text=True)

    _set_diag(app, display_id, json.dumps({"media_support": {"checked": 2, "problems": []}}))
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert "Playback warnings" not in page and "can play all 2 videos" in page
    assert "May stutter" not in admin_client.get("/displays/").get_data(as_text=True)


def test_manifest_gives_the_player_the_video_facts(app, configured, client, paired_display):
    from aisignx.models import Media, Playlist, PlaylistItem
    with app.app_context():
        pl = db.session.get(Display, paired_display[0]).playlist
        if pl is None:
            pl = Playlist(name="P")
            db.session.add(pl)
            db.session.get(Display, paired_display[0]).playlist = pl
        known = Media(name="Known", media_type="video", stored_name="a.mp4", mime_type="video/mp4", file_size=1,
                      checksum_sha256="a" * 64, width=3840, height=2160, duration_seconds=5,
                      video_info={"codec": "h264", "profile": "High", "level": 51, "fps": 25.0, "bitrate": 40_000_000})
        unknown = Media(name="Unknown", media_type="video", stored_name="b.mp4", mime_type="video/mp4", file_size=1,
                        checksum_sha256="b" * 64, width=640, height=360, duration_seconds=5)
        pl.items.append(PlaylistItem(media=known, position=0))
        pl.items.append(PlaylistItem(media=unknown, position=1))
        db.session.commit()
    resp = client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN))
    items = resp.get_json()["items"]
    assert items[0]["video"] == {"codec": "avc1.640033", "width": 3840, "height": 2160, "fps": 25.0, "bitrate": 40_000_000}
    assert "video" not in items[1]  # nothing known: the player has nothing to ask
