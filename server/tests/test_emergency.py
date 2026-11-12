"""Emergency override: admin routes, validation, manifest object, SSE events, history."""
import queue
import re

import pytest

from aisignx.extensions import db
from aisignx.models import Display, DisplayGroup, EmergencyMessage, Media, Playlist, PlaylistItem
from aisignx.player.auth import hash_token
from aisignx.player.broker import QUEUE_SIZE, get_broker
from aisignx.timeutil import utcnow

from .conftest import DISPLAY_TOKEN, bearer, login, make_user
from .test_player_stream import KEEPALIVE, MAX_AGE, _open, _read

API = "/api/player/v1"
OTHER_TOKEN = "other-display-token-0123456789abcdefghijklmnopq"


def _as(client, app, role):
    make_user(app, role, role)
    login(client, role)
    return client


def _drain(sub) -> list:
    events = []
    while True:
        try:
            events.append(sub.queue.get_nowait())
        except queue.Empty:
            return events


def _emergency_events(sub) -> list:
    return [e for e in _drain(sub) if e[0] in ("emergency", "emergency_clear")]


@pytest.fixture
def other_display(app, configured):
    """A second paired display, in group "Hall"."""
    with app.app_context():
        group = DisplayGroup(name="Hall")
        display = Display(name="Hall screen", token_hash=hash_token(OTHER_TOKEN),
                          paired_at=utcnow(), group=group)
        db.session.add(display)
        db.session.commit()
        return display.id, group.id


@pytest.fixture
def subs(app, paired_display, other_display):
    """Broker subscribers for (Lobby, Hall screen)."""
    with app.app_context():
        broker = get_broker()
        pair = broker.subscribe(paired_display[0]), broker.subscribe(other_display[0])
    yield pair
    for sub in pair:
        broker.unsubscribe(sub)


def _playlist(app, playable=True) -> int:
    with app.app_context():
        playlist = Playlist(name="Evacuation" if playable else "Empty")
        if playable:
            media = Media(name="Exit map", media_type="web", url="https://example.com/exit",
                          default_duration=20)
            playlist.items.append(PlaylistItem(media=media, position=0))
        db.session.add(playlist)
        db.session.commit()
        return playlist.id


def _send(client, follow_redirects=False, **fields):
    data = {"title": "Fire drill", "message": "Leave by the nearest exit.", "level": "critical",
            "target": "all", "playlist": 0, "show_text": "y"}
    data.update(fields)
    data = {k: v for k, v in data.items() if v is not None}
    return client.post("/emergency/send", data=data, follow_redirects=follow_redirects)


def _manifest(client, token=DISPLAY_TOKEN) -> dict:
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    assert resp.status_code == 200
    return resp.get_json()


def _live_ids(app) -> list[int]:
    with app.app_context():
        return [m.id for m in EmergencyMessage.query.filter(EmergencyMessage.cleared_at.is_(None))
                .order_by(EmergencyMessage.id)]


# --- Role matrix ------------------------------------------------------------------------

def test_viewer_can_read_but_not_send_or_clear(configured, client):
    with configured.app_context():
        db.session.add(EmergencyMessage(title="Drill", scope="all", target_label="All screens"))
        db.session.commit()
    _as(client, configured, "viewer")
    page = client.get("/emergency/")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Drill" in html and "Send emergency message" not in html
    assert ">Clear<" not in html and "Send again" not in html
    assert _send(client).status_code == 403
    assert client.post("/emergency/1/clear").status_code == 403
    assert client.post("/emergency/clear-all").status_code == 403
    assert _live_ids(configured) == [1]


def test_anonymous_is_redirected(configured, client):
    assert client.get("/emergency/").status_code == 302


@pytest.mark.parametrize("role", ["editor", "admin"])
def test_editor_and_admin_can_send_and_clear(configured, client, role):
    if role == "admin":
        login(client, "admin")
    else:
        _as(client, configured, role)
    assert "Send emergency message" in client.get("/emergency/").get_data(as_text=True)
    resp = _send(client)
    assert resp.status_code == 302
    [msg_id] = _live_ids(configured)
    assert client.post(f"/emergency/{msg_id}/clear").status_code == 302
    assert _live_ids(configured) == []


# --- Validation -------------------------------------------------------------------------

@pytest.mark.parametrize("fields", [
    {"target": "d:999"},
    {"target": "g:999"},
    {"target": "bogus"},
    {"target": None},
    {"title": ""},
    {"title": "x" * 121},
    {"message": "x" * 1001},
    {"level": "panic"},
    {"playlist": 999},
    {"show_text": None},  # no playlist: text must show
])
def test_invalid_send_is_rejected(admin_client, configured, fields):
    resp = _send(admin_client, **fields)
    assert resp.status_code == 400
    assert "was not sent" in resp.get_data(as_text=True)
    assert _live_ids(configured) == []


def test_unplayable_playlist_is_rejected(admin_client, configured):
    empty = _playlist(configured, playable=False)
    resp = _send(admin_client, playlist=empty)
    assert resp.status_code == 400
    assert "nothing the screens can play" in resp.get_data(as_text=True)


def test_playlist_without_text(admin_client, configured, paired_display):
    playlist_id = _playlist(configured)
    assert _send(admin_client, playlist=playlist_id, show_text=None).status_code == 302
    with configured.app_context():
        msg = EmergencyMessage.query.one()
        assert (msg.playlist_id, msg.show_text) == (playlist_id, False)
    manifest = _manifest(admin_client)
    assert manifest["emergency"]["layout"] == "none"
    assert manifest["source"]["type"] == "emergency"
    assert manifest["playlist"]["id"] == playlist_id


def test_targets_and_labels(admin_client, configured, paired_display, other_display):
    display_id, group_id = other_display
    _send(admin_client, target=f"g:{group_id}")
    _send(admin_client, target=f"d:{paired_display[0]}", level="info")
    with configured.app_context():
        msgs = EmergencyMessage.query.order_by(EmergencyMessage.id).all()
        assert [(m.scope, m.group_id, m.display_id, m.target_label) for m in msgs] == [
            ("group", group_id, None, "Group Hall"),
            ("display", None, paired_display[0], "Display Lobby"),
        ]


# --- Manifest ---------------------------------------------------------------------------

def test_manifest_emergency_object_and_version(admin_client, configured, paired_display):
    before = _manifest(admin_client)
    assert before["emergency"] is None

    resp = _send(admin_client, level="warning", follow_redirects=True)
    _send(admin_client, level="warning")
    with configured.app_context():
        first = EmergencyMessage.query.order_by(EmergencyMessage.id).first().id
    sent = _manifest(admin_client)
    em = sent["emergency"]
    assert em["level"] == "warning"
    assert (em["background"], em["foreground"]) == ("#e65100", "#ffffff")
    assert em["layout"] == "fullscreen"
    assert em["title"] == "Fire drill" and em["message"] == "Leave by the nearest exit."
    assert em["since"].endswith("Z")
    assert em["id"] == first + 1  # newest wins within a level
    assert sent["version"] != before["version"]

    admin_client.post("/emergency/clear-all")
    cleared = _manifest(admin_client)
    assert cleared["emergency"] is None
    assert cleared["version"] != sent["version"]
    assert resp.status_code == 200


def test_critical_beats_newer_warning_in_manifest(admin_client, configured, paired_display):
    _send(admin_client, level="critical", title="Fire")
    _send(admin_client, level="warning", title="Spill")
    em = _manifest(admin_client)["emergency"]
    assert (em["title"], em["background"]) == ("Fire", "#b71c1c")


# --- Events -----------------------------------------------------------------------------

def test_send_reaches_only_targeted_subscribers(admin_client, configured, subs, other_display):
    lobby, hall = subs
    _drain(lobby), _drain(hall)
    resp = _send(admin_client, target=f"g:{other_display[1]}", follow_redirects=True)
    assert "sent to 1 screen." in resp.get_data(as_text=True)
    assert _emergency_events(lobby) == []
    [(event, data)] = _emergency_events(hall)
    assert event == "emergency"
    assert data["title"] == "Fire drill" and data["layout"] == "fullscreen"


def test_send_to_all_counts_paired_screens(admin_client, configured, subs):
    resp = _send(admin_client, follow_redirects=True)
    assert "sent to 2 screens." in resp.get_data(as_text=True)


def test_clear_resends_remaining_then_clears(admin_client, configured, subs):
    lobby, _ = subs
    _send(admin_client, title="A", level="critical")
    _send(admin_client, title="B", level="warning")
    a, b = _live_ids(configured)
    _drain(lobby)

    admin_client.post(f"/emergency/{a}/clear")
    [(event, data)] = _emergency_events(lobby)
    assert (event, data["id"], data["title"]) == ("emergency", b, "B")

    admin_client.post(f"/emergency/{b}/clear")
    assert _emergency_events(lobby) == [("emergency_clear", {"id": b})]


def test_clear_and_clear_all_are_idempotent(admin_client, configured, subs):
    lobby, _ = subs
    _send(admin_client)
    [msg_id] = _live_ids(configured)
    admin_client.post(f"/emergency/{msg_id}/clear")
    with configured.app_context():
        first_clear = db.session.get(EmergencyMessage, msg_id).cleared_at
    _drain(lobby)

    resp = admin_client.post(f"/emergency/{msg_id}/clear", follow_redirects=True)
    assert "already cleared" in resp.get_data(as_text=True)
    resp = admin_client.post("/emergency/clear-all", follow_redirects=True)
    assert "No emergency message is live" in resp.get_data(as_text=True)
    assert _drain(lobby) == []
    with configured.app_context():
        msg = db.session.get(EmergencyMessage, msg_id)
        assert msg.cleared_at == first_clear and msg.clear_reason == "manual"
    assert admin_client.post("/emergency/999/clear").status_code == 404


def test_clear_all_clears_every_live_message(admin_client, configured, subs):
    lobby, _ = subs
    _send(admin_client, title="A")
    _send(admin_client, title="B")
    _drain(lobby)
    resp = admin_client.post("/emergency/clear-all", follow_redirects=True)
    assert "Cleared 2 emergency messages" in resp.get_data(as_text=True)
    assert _live_ids(configured) == []
    events = _emergency_events(lobby)
    assert events[-1][0] == "emergency_clear"


def test_forced_event_is_delivered_when_queue_is_full(admin_client, configured, subs):
    lobby, _ = subs
    with configured.app_context():
        broker = get_broker()
        for _ in range(QUEUE_SIZE + 5):
            broker.publish(lobby.display_id, "manifest", {})
    assert lobby.queue.full()
    _send(admin_client)
    assert any(e[0] == "emergency" for e in _drain(lobby))


# --- SSE --------------------------------------------------------------------------------

def test_sse_reconnect_sends_emergency_after_hello(app, admin_client, configured, paired_display):
    app.config.update(PLAYER_SSE_KEEPALIVE_SECONDS=KEEPALIVE, PLAYER_SSE_MAX_AGE_SECONDS=MAX_AGE)
    _send(admin_client, title="Drill")
    resp = _open(admin_client)
    try:
        frames, _ = _read(resp, until=lambda f: len(f) >= 4)
    finally:
        resp.close()
    named = [f for f in frames if f[0] not in ("comment", "retry")]
    assert named[0][0] == "hello"
    event, data = named[1]
    assert event == "emergency" and data["title"] == "Drill"


def test_sse_without_emergency_has_no_extra_frame(app, admin_client, configured, paired_display):
    app.config.update(PLAYER_SSE_KEEPALIVE_SECONDS=KEEPALIVE, PLAYER_SSE_MAX_AGE_SECONDS=MAX_AGE)
    resp = _open(admin_client)
    try:
        frames, _ = _read(resp, until=lambda f: len(f) >= 4)
    finally:
        resp.close()
    named = [f for f in frames if f[0] not in ("comment", "retry")]
    assert named[0][0] == "hello"
    assert all(f[0] != "emergency" for f in named)


# --- History, prefill, banner -----------------------------------------------------------

def test_display_delete_keeps_history(admin_client, configured, paired_display):
    display_id = paired_display[0]
    _send(admin_client, target=f"d:{display_id}")
    assert admin_client.post(f"/displays/{display_id}/delete").status_code == 302
    with configured.app_context():
        msg = EmergencyMessage.query.one()
        assert (msg.target_label, msg.clear_reason) == ("Display Lobby", "target_deleted")
        assert msg.cleared_at is not None and msg.display_id is None
    html = admin_client.get("/emergency/").get_data(as_text=True)
    assert "Display Lobby" in html and "target deleted" in html


def test_history_shows_sender_and_local_time(admin_client, configured):
    with configured.app_context():
        from aisignx.models import Setting
        Setting.set("timezone", "Europe/Berlin")
        db.session.commit()
    _send(admin_client, title="Drill")
    html = admin_client.get("/emergency/").get_data(as_text=True)
    assert "Drill" in html and "admin" in html
    assert "CET" in html or "CEST" in html


def test_send_again_prefills_form(admin_client, configured, paired_display):
    _send(admin_client, title="Drill", message="Line one", level="info",
          target=f"d:{paired_display[0]}")
    [msg_id] = _live_ids(configured)
    html = admin_client.get(f"/emergency/?from={msg_id}").get_data(as_text=True)
    assert 'value="Drill"' in html and "Line one" in html
    assert f'selected value="d:{paired_display[0]}"' in html
    assert 'selected value="info"' in html


def test_send_again_never_widens_a_deleted_target(admin_client, configured):
    with configured.app_context():
        db.session.add(EmergencyMessage(title="Old", scope="display", display_id=None,
                                        target_label="Display Gone", cleared_at=utcnow()))
        db.session.commit()
    html = admin_client.get("/emergency/?from=1").get_data(as_text=True)
    select = re.search(r'<select[^>]*name="target".*?</select>', html, re.S).group(0)
    options = re.findall(r"<option[^>]*>", select)
    # The empty placeholder comes first, and "All screens" is not preselected, so the
    # browser submits nothing (rejected) rather than silently widening to every screen.
    assert 'value=""' in options[0]
    assert not any("selected" in o and 'value="all"' in o for o in options)


def test_send_without_target_is_rejected(app, admin_client, configured):
    resp = admin_client.post("/emergency/send", data={"title": "Drill", "level": "critical",
                                                      "target": "", "playlist": "0",
                                                      "show_text": "y"})
    assert resp.status_code in (200, 302, 400)  # re-rendered form, never a 500
    with app.app_context():
        assert EmergencyMessage.query.count() == 0


@pytest.mark.parametrize("target", ["d:99999999999999999999", "g:99999999999999999999"])
def test_huge_target_id_is_a_form_error_not_500(app, admin_client, configured, target):
    resp = admin_client.post("/emergency/send", data={"title": "Drill", "level": "critical",
                                                      "target": target, "playlist": "0",
                                                      "show_text": "y"})
    assert resp.status_code in (200, 302, 400)  # re-rendered form, never a 500
    with app.app_context():
        assert EmergencyMessage.query.count() == 0


def test_banner_shows_while_live(admin_client, configured):
    assert "Emergency active" not in admin_client.get("/dashboard").get_data(as_text=True)
    _send(admin_client)
    html = admin_client.get("/dashboard").get_data(as_text=True)
    assert "Emergency active" in html
