"""Identify, screenshot requests and uploads, and the diagnostics refresh command."""
import json
from datetime import timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import update

from aisignx.displays import screenshots
from aisignx.extensions import db
from aisignx.models import Display
from aisignx.player.broker import get_broker
from aisignx.timeutil import utcnow

from .conftest import DISPLAY_TOKEN, bearer, login, make_user, png_bytes
from .test_player_stream import _drain, _open, _read, listener, sse_app  # noqa: F401

API = "/api/player/v1"
ACTIONS = ("identify", "screenshot", "diagnostics/refresh")


def _role_client(app, client, role):
    if role != "admin":
        make_user(app, role, role)
    login(client, role)
    return client


def _subscribe(app, display_id):
    with app.app_context():
        return get_broker().subscribe(display_id)


def _request_id(app, display_id) -> str | None:
    with app.app_context():
        return db.session.get(Display, display_id).screenshot_request


def _open_request(app, display_id, age_s: float = 0) -> str:
    rid = "0123456789abcdef"
    with app.app_context():
        db.session.execute(update(Display).where(Display.id == display_id).values(
            screenshot_request=rid,
            screenshot_requested_at=utcnow() - timedelta(seconds=age_s)))
        db.session.commit()
    return rid


def _upload(client, rid, data=None, content_type="image/png", token=DISPLAY_TOKEN):
    headers = {"X-Screenshot-Request": rid}
    if token:
        headers.update(bearer(token))
    return client.post(f"{API}/screenshot", data=png_bytes() if data is None else data,
                       content_type=content_type, headers=headers)


# --- admin actions ----------------------------------------------------------------------

@pytest.mark.parametrize("role,allowed", [("viewer", False), ("editor", True), ("admin", True)])
def test_action_roles(configured, client, paired_display, role, allowed):
    display_id = paired_display[0]
    _role_client(configured, client, role)
    for action in ACTIONS:
        resp = client.post(f"/displays/{display_id}/{action}")
        assert resp.status_code == (302 if allowed else 403), action
    # Any logged-in user may look at the screenshot (404: none taken yet).
    assert client.get(f"/displays/{display_id}/screenshot.jpg").status_code == 404


def test_actions_need_login(configured, client, paired_display):
    display_id = paired_display[0]
    for action in ACTIONS:
        assert client.post(f"/displays/{display_id}/{action}").status_code == 302
    resp = client.get(f"/displays/{display_id}/screenshot.jpg")
    assert resp.status_code == 302 and "/login" in resp.headers["Location"]


def test_identify_and_refresh_send_commands(app, admin_client, paired_display):
    display_id = paired_display[0]
    sub = _subscribe(app, display_id)
    resp = admin_client.post(f"/displays/{display_id}/identify", follow_redirects=True)
    assert b"15 seconds" in resp.data
    admin_client.post(f"/displays/{display_id}/diagnostics/refresh")
    assert _drain(sub) == [("command", {"action": "identify", "seconds": 15}),
                           ("command", {"action": "diagnostics"})]


def test_actions_not_connected_flash(app, admin_client, paired_display):
    display_id = paired_display[0]
    for action in ("identify", "diagnostics/refresh"):
        resp = admin_client.post(f"/displays/{display_id}/{action}", follow_redirects=True)
        assert b"The display is not connected right now." in resp.data
    resp = admin_client.post(f"/displays/{display_id}/screenshot", follow_redirects=True)
    assert b"the request stays open for 2 minutes" in resp.data
    assert _request_id(app, display_id) is not None
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert f'data-screenshot-pending="{display_id}"' in page


def test_screenshot_request_sends_command_and_throttles(app, admin_client, paired_display,
                                                        listener):  # noqa: F811
    display_id = paired_display[0]
    sub = _subscribe(app, display_id)
    _drain(listener)
    admin_client.post(f"/displays/{display_id}/screenshot")
    rid = _request_id(app, display_id)
    assert len(rid) == 16
    assert _drain(sub) == [("command", {"action": "screenshot", "request_id": rid})]
    assert _drain(listener) == []  # a Core UPDATE: no fleet-wide manifest hint

    resp = admin_client.post(f"/displays/{display_id}/screenshot", follow_redirects=True)
    assert b"A screenshot was just requested." in resp.data
    assert _request_id(app, display_id) == rid and _drain(sub) == []

    _open_request(app, display_id, age_s=screenshots.RESEND_AFTER_S + 1)
    admin_client.post(f"/displays/{display_id}/screenshot")
    assert _request_id(app, display_id) not in (None, "0123456789abcdef")


def test_screenshot_request_needs_paired_display(app, admin_client):
    with app.app_context():
        display = Display(name="Spare")
        db.session.add(display)
        db.session.commit()
        display_id = display.id
    resp = admin_client.post(f"/displays/{display_id}/screenshot", follow_redirects=True)
    assert b"not paired" in resp.data
    assert _request_id(app, display_id) is None


def test_pending_request_resent_on_connect(sse_app, client, paired_display):  # noqa: F811
    display_id = paired_display[0]
    rid = _open_request(sse_app, display_id)
    resp = _open(client)
    try:
        frames, _ = _read(resp, until=lambda f: any(e == "command" for e, _ in f))
    finally:
        resp.close()
    assert ("command", {"action": "screenshot", "request_id": rid}) in frames

    _open_request(sse_app, display_id, age_s=screenshots.REQUEST_TTL_S + 1)
    resp = _open(client)
    try:
        frames, _ = _read(resp)
    finally:
        resp.close()
    assert not [f for f in frames if f[0] == "command"]


# --- upload -----------------------------------------------------------------------------

def test_upload_stores_jpeg(app, client, configured, paired_display, listener):  # noqa: F811
    display_id = paired_display[0]
    rid = _open_request(app, display_id)
    _drain(listener)
    resp = _upload(client, rid, png_bytes((3000, 1500)))
    assert resp.status_code == 200 and resp.get_json() == {"status": "saved"}
    assert _drain(listener) == []
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display.screenshot_request is None and display.screenshot_requested_at is None
        assert display.screenshot_at is not None
        with Image.open(screenshots.path_for(display_id)) as img:
            assert img.format == "JPEG" and max(img.size) == 1920
    # Reusing the same id fails: the request was claimed.
    assert _upload(client, rid).status_code == 409


def test_upload_rejections(app, client, configured, paired_display):
    display_id = paired_display[0]
    rid = _open_request(app, display_id)
    assert _upload(client, rid, token=None).status_code == 401
    resp = _upload(client, "fedcba9876543210")
    assert resp.status_code == 409 and resp.get_json()["error"] == "stale_request"
    assert _upload(client, "not-hex").status_code == 409
    resp = _upload(client, rid, b"hello", content_type="text/plain")
    assert resp.status_code == 415 and resp.get_json()["error"] == "unsupported_type"
    resp = _upload(client, rid, b"\x89PNG garbage", content_type="image/png")
    assert resp.status_code == 400 and resp.get_json()["error"] == "bad_image"
    resp = _upload(client, rid, b"\0" * (screenshots_max() + 1), content_type="image/jpeg")
    assert resp.status_code == 413 and resp.get_json()["error"] == "too_large"
    assert _request_id(app, display_id) == rid  # still open after the failures

    _open_request(app, display_id, age_s=screenshots.REQUEST_TTL_S + 1)
    assert _upload(client, rid).status_code == 409


def screenshots_max() -> int:
    from aisignx.player.api import SCREENSHOT_MAX
    return SCREENSHOT_MAX


def test_heartbeat_body_limit_unchanged(app, client, configured, paired_display):
    big = json.dumps({"client_id": "abc", "player_version": "x" * 9000})
    resp = client.post(f"{API}/heartbeat", headers=bearer(DISPLAY_TOKEN), data=big,
                       content_type="application/json")
    assert resp.status_code == 413


def test_process_rejects_pixel_bombs_and_strips_exif():
    with pytest.raises(ValueError):
        screenshots.process(png_bytes((8000, 6000)))  # 48 MP
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    buf = BytesIO()
    Image.new("RGB", (40, 20)).save(buf, "JPEG", exif=exif)
    out = screenshots.process(buf.getvalue())
    assert b"SecretCam" in buf.getvalue() and b"SecretCam" not in out


def test_image_route_and_delete(app, admin_client, paired_display):
    display_id = paired_display[0]
    rid = _open_request(app, display_id)
    assert _upload(admin_client, rid).status_code == 200
    resp = admin_client.get(f"/displays/{display_id}/screenshot.jpg")
    assert resp.status_code == 200 and resp.mimetype == "image/jpeg"
    assert resp.headers["Cache-Control"] == "no-store"
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert f"/displays/{display_id}/screenshot.jpg?t=" in page

    status = admin_client.get("/displays/status.json").get_json()["displays"][0]
    assert status["screenshot_at"].endswith("Z") and status["screenshot_pending"] is False

    admin_client.post(f"/displays/{display_id}/delete")
    with app.app_context():
        assert not screenshots.path_for(display_id).exists()


def test_unpair_clears_request(app, admin_client, paired_display):
    display_id = paired_display[0]
    _open_request(app, display_id)
    admin_client.post(f"/displays/{display_id}/unpair")
    assert _request_id(app, display_id) is None
