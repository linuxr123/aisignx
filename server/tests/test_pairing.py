"""Pairing a screen: /pair, /pair/status, and the approve/claim service.

Most tests approve codes by calling displays.pairing.approve() directly (as the admin
route does). Tests that go through the admin route POST /displays/pair are grouped at
the bottom under "ADMIN ROUTE" and have `_via_admin_route` in their names.
"""
import hashlib
import re
from datetime import timedelta

import pytest
from sqlalchemy import update

from aisignx.displays import pairing
from aisignx.extensions import db
from aisignx.models import Display, Media, PairingCode, Playlist, PlaylistItem
from aisignx.player.auth import hash_token
from aisignx.player.broker import get_broker
from aisignx.timeutil import as_utc, utcnow

from .conftest import bearer, login, make_user

API = "/api/player/v1"
CODE_RE = re.compile(r"^[2-9A-HJKMNP-Z]{6}$")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _pair(client, ip="10.0.0.1", **body):
    return client.post(f"{API}/pair", json=body, environ_base={"REMOTE_ADDR": ip})


def _start(client, ip="10.0.0.1", **body):
    resp = _pair(client, ip=ip, **body)
    assert resp.status_code == 201, resp.get_json()
    return resp.get_json()


def _status(client, secret, credential=None):
    body = {"pairing_secret": secret}
    if credential:
        body["credential"] = credential
    return client.post(f"{API}/pair/status", json=body)


def _approve(app, code, name="Lobby", playlist_id=None, replace_id=None):
    """Approve a code the way the admin route does. Returns the display id or None."""
    with app.app_context():
        replace = db.session.get(Display, replace_id) if replace_id else None
        display = pairing.approve(code, name, playlist_id, replace, None)
        if display is None:
            db.session.rollback()
            return None
        db.session.commit()
        return display.id


def _expire(app, code):
    with app.app_context():
        db.session.execute(update(PairingCode).where(PairingCode.code == code)
                           .values(expires_at=utcnow() - timedelta(seconds=5)))
        db.session.commit()


def _pending_codes(app, ip=None):
    with app.app_context():
        query = PairingCode.query.filter(PairingCode.display_id.is_(None))
        if ip:
            query = query.filter(PairingCode.client_ip == ip)
        return sorted(row.code for row in query)


# --- helpers ----------------------------------------------------------------------------

def test_normalize_and_display_code():
    assert pairing.normalize_code(" k7p-q4x ") == "K7PQ4X"
    assert pairing.normalize_code("k7p q4x") == "K7PQ4X"
    assert pairing.normalize_code("K7PQ4O") is None  # O is not in the alphabet
    assert pairing.normalize_code("K7PQ41") is None  # neither is 1
    assert pairing.normalize_code("K7PQ4") is None
    assert pairing.normalize_code("K7PQ4XX") is None
    assert pairing.normalize_code("") is None
    assert pairing.normalize_code(None) is None
    assert pairing.display_code("K7PQ4X") == "K7P-Q4X"
    assert not set("01OIL") & set(pairing.ALPHABET)
    assert len(pairing.ALPHABET) == 31


# --- /pair ------------------------------------------------------------------------------

def test_pair_returns_code_and_stores_only_secret_hash(app, configured, client):
    resp = _pair(client, client_id="abc123", device="Chrome 130 / Windows",
                 player_version="web-3.0.0")
    assert resp.status_code == 201
    assert resp.headers["Cache-Control"] == "no-store"
    data = resp.get_json()
    assert CODE_RE.match(data["code"])
    assert data["display_code"] == f"{data['code'][:3]}-{data['code'][3:]}"
    assert len(data["pairing_secret"]) == 43
    assert data["expires_in"] == 600
    assert data["poll_after_s"] == 3

    with app.app_context():
        row = PairingCode.query.one()
        assert row.code == data["code"]
        assert row.secret_hash == _sha(data["pairing_secret"])
        assert data["pairing_secret"] not in (row.code, row.secret_hash, row.client_id)
        assert row.client_ip == "10.0.0.1"
        assert row.client_id == "abc123"
        assert row.device_info == "Chrome 130 / Windows"
        assert row.display_id is None
        remaining = as_utc(row.expires_at) - utcnow()
        assert timedelta(seconds=590) < remaining <= timedelta(seconds=600)


def test_pair_accepts_empty_body_and_truncates_device(app, configured, client):
    assert client.post(f"{API}/pair").status_code == 201
    _start(client, device="x" * 500)
    with app.app_context():
        assert max(len(r.device_info or "") for r in PairingCode.query) == 120


@pytest.mark.parametrize("body", [
    {"client_id": "has space"}, {"client_id": "x" * 65}, {"client_id": 42}, {"client_id": ""},
])
def test_pair_rejects_bad_client_id(app, configured, client, body):
    resp = client.post(f"{API}/pair", json=body)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_request"


@pytest.mark.parametrize("raw", [b"[1, 2]", b"not json", b'"text"'])
def test_pair_rejects_non_object_body(app, configured, client, raw):
    resp = client.post(f"{API}/pair", data=raw, content_type="application/json")
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_request"


# --- /pair/status -----------------------------------------------------------------------

def test_status_validation(app, configured, client):
    assert client.post(f"{API}/pair/status", json={}).status_code == 400
    assert client.post(f"{API}/pair/status", json={"pairing_secret": 5}).status_code == 400
    assert client.post(f"{API}/pair/status",
                       json={"pairing_secret": "x" * 200}).status_code == 400
    resp = client.post(f"{API}/pair/status",
                       json={"pairing_secret": "abc", "credential": "query"})
    assert resp.status_code == 400
    resp = _status(client, "no-such-secret")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "expired"


def test_full_bearer_flow(app, configured, client):
    data = _start(client)
    resp = _status(client, data["pairing_secret"])
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "pending"
    assert 590 <= body["expires_in"] <= 600

    display_id = _approve(app, data["code"].lower(), name="Lobby")
    assert display_id is not None

    resp = _status(client, data["pairing_secret"])  # default credential is bearer
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "paired"
    assert body["display"] == {"id": display_id, "name": "Lobby"}
    assert body["heartbeat_s"] == 30
    token = body["token"]
    assert len(token) == 43
    assert "Set-Cookie" not in resp.headers

    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display.token_hash == _sha(token)
        assert display.paired_at is not None
        assert PairingCode.query.count() == 0  # deleted at claim: single use

    again = _status(client, data["pairing_secret"])
    assert again.status_code == 404
    assert again.get_json()["error"] == "expired"

    manifest = client.get(f"{API}/manifest", headers=bearer(token))
    assert manifest.status_code == 200
    assert manifest.get_json()["display"]["id"] == display_id


def test_cookie_flow(app, configured, client):
    data = _start(client)
    _approve(app, data["code"])
    resp = _status(client, data["pairing_secret"], credential="cookie")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["status"] == "paired"
    assert "token" not in body

    cookies = [c for c in resp.headers.getlist("Set-Cookie") if c.startswith("aisignx_display=")]
    assert len(cookies) == 1
    cookie = cookies[0]
    attrs = {part.strip().lower() for part in cookie.split(";")}
    assert "httponly" in attrs
    assert "samesite=strict" in attrs
    assert "path=/api/player" in attrs
    assert "secure" not in attrs  # SESSION_COOKIE_SECURE is off in tests
    raw = cookie.split(";", 1)[0].split("=", 1)[1]
    with app.app_context():
        assert Display.query.one().token_hash == hash_token(raw)

    # The test client sends the cookie back on /api/player paths.
    assert client.get(f"{API}/manifest").status_code == 200


def test_cookie_secure_flag_follows_config(app, configured, client):
    app.config["SESSION_COOKIE_SECURE"] = True
    data = _start(client)
    _approve(app, data["code"])
    resp = _status(client, data["pairing_secret"], credential="cookie")
    cookie = next(c for c in resp.headers.getlist("Set-Cookie")
                  if c.startswith("aisignx_display="))
    assert "secure" in {p.strip().lower() for p in cookie.split(";")}


def test_expired_code(app, configured, client):
    first = _start(client, ip="10.0.0.1")
    second = _start(client, ip="10.0.0.2")
    _expire(app, first["code"])
    _expire(app, second["code"])

    assert _approve(app, first["code"]) is None
    with app.app_context():
        assert Display.query.count() == 0

    resp = _status(client, first["pairing_secret"])
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "expired"

    # The next /pair purges every expired row (the second one was never polled).
    fresh = _start(client, ip="10.0.0.3")
    with app.app_context():
        assert [r.code for r in PairingCode.query] == [fresh["code"]]


def test_approve_rejects_bad_and_unknown_codes(app, configured):
    assert _approve(app, "nope") is None
    assert _approve(app, "K7PQ4O") is None
    assert _approve(app, "222222") is None  # well-formed but unknown
    with app.app_context():
        assert Display.query.count() == 0


def test_per_ip_cap_refuses_instead_of_evicting(app, configured, client):
    """Screens behind one IP must not knock out each other's codes (review finding)."""
    app.config["PAIRING_MAX_PENDING_PER_IP"] = 3
    codes = [_start(client, ip="10.0.0.9", client_id=f"screen{i}")["code"] for i in range(3)]
    resp = _pair(client, ip="10.0.0.9", client_id="screen3")
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "too_many_pending"
    assert _pending_codes(app, "10.0.0.9") == sorted(codes)  # nobody was evicted
    # Other IPs are unaffected.
    other = _start(client, ip="10.0.0.10")["code"]
    assert _pending_codes(app, "10.0.0.10") == [other]


def test_same_client_replaces_its_own_pending_code(app, configured, client):
    app.config["PAIRING_MAX_PENDING_PER_IP"] = 3
    others = [_start(client, ip="10.0.0.9", client_id=f"screen{i}")["code"] for i in range(2)]
    first = _start(client, ip="10.0.0.9", client_id="reloader")
    again = _start(client, ip="10.0.0.9", client_id="reloader")  # at the cap, but its own row
    assert _pending_codes(app, "10.0.0.9") == sorted(others + [again["code"]])
    assert _status(client, first["pairing_secret"]).status_code == 404


def test_global_pending_cap(app, configured, client):
    for i in range(50):  # three per IP, so no IP hits its own cap first
        _start(client, ip=f"10.1.0.{i // 3}", client_id=f"c{i}")
    resp = _pair(client, ip="10.1.1.1")
    assert resp.status_code == 429
    assert resp.get_json()["error"] == "too_many_pending"
    assert resp.headers["Retry-After"] == "60"
    with app.app_context():
        assert PairingCode.query.count() == 50

    # A device asking again replaces its own code, so it is never locked out by the cap.
    assert _pair(client, ip="10.1.0.0", client_id="c0").status_code == 201
    with app.app_context():
        assert PairingCode.query.count() == 50


def test_approved_codes_do_not_count_as_pending(app, configured, client):
    codes = [_start(client, ip="10.0.0.5")["code"] for _ in range(3)]
    _approve(app, codes[0])
    _start(client, ip="10.0.0.5")  # only two pending, so nothing is replaced
    with app.app_context():
        assert PairingCode.query.count() == 4
        assert PairingCode.query.filter(PairingCode.display_id.isnot(None)).count() == 1


def test_approve_twice_only_one_wins(app, configured, client):
    data = _start(client)
    assert _approve(app, data["code"], name="A") is not None
    assert _approve(app, data["code"], name="B") is None
    with app.app_context():
        assert [d.name for d in Display.query] == ["A"]


def test_approve_race_conditional_update(app, configured, client, paired_display, monkeypatch):
    """Another editor approves the same code between our SELECT and our UPDATE."""
    data = _start(client)
    other_id, _ = paired_display
    real_flush = db.session.flush

    def flush_then_race(*args, **kwargs):
        real_flush(*args, **kwargs)
        db.session.execute(update(PairingCode).where(PairingCode.code == data["code"])
                           .values(display_id=other_id))

    with app.app_context():
        monkeypatch.setattr(db.session, "flush", flush_then_race)
        assert pairing.approve(data["code"], "B", None, None, None) is None
        monkeypatch.setattr(db.session, "flush", real_flush)
        db.session.rollback()
        assert [d.name for d in Display.query] == ["Lobby"]  # no second display created


def test_unpair_cancels_unclaimed_approval(app, configured, client):
    from aisignx.displays import service

    data = _start(client)
    display_id = _approve(app, data["code"])
    with app.app_context():
        service.unpair(db.session.get(Display, display_id))
        db.session.commit()
    assert _status(client, data["pairing_secret"]).status_code == 404
    with app.app_context():
        assert db.session.get(Display, display_id).token_hash is None


def test_pair_as_replaces_earlier_unclaimed_approval(app, configured, client, paired_display):
    display_id, _ = paired_display
    first = _start(client, client_id="dev1")
    second = _start(client, client_id="dev2")
    assert _approve(app, first["code"], replace_id=display_id) == display_id
    assert _approve(app, second["code"], replace_id=display_id) == display_id
    assert _status(client, first["pairing_secret"]).status_code == 404  # superseded
    assert _status(client, second["pairing_secret"]).get_json()["status"] == "paired"


def test_approved_but_unclaimed_row_gets_claim_window(app, configured, client):
    data = _start(client)
    before = utcnow()
    display_id = _approve(app, data["code"])
    with app.app_context():
        row = PairingCode.query.one()
        assert row.display_id == display_id
        expires = as_utc(row.expires_at)
        claim = timedelta(seconds=app.config["PAIRING_CLAIM_SECONDS"])
        assert before + claim - timedelta(seconds=2) <= expires <= utcnow() + claim
        assert db.session.get(Display, display_id).device_info is None

    # The claim window can also run out before the device polls.
    _expire(app, data["code"])
    assert _status(client, data["pairing_secret"]).status_code == 404


def test_device_info_copied_to_display(app, configured, client):
    data = _start(client, device="Tizen TV")
    display_id = _approve(app, data["code"], name="")
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display.device_info == "Tizen TV"
        assert display.name == "Display"  # blank name falls back
        assert display.token_hash is None  # not paired until the device claims


def test_pair_as_existing_display(app, configured, client, paired_display):
    display_id, old_token = paired_display
    with app.app_context():
        playlist = Playlist(name="Morning")
        # Needs a playable item: an empty Default playlist resolves to source "none".
        playlist.items.append(PlaylistItem(position=0, media=Media(
            name="Menu", media_type="web", url="https://example.com/menu")))
        db.session.add(playlist)
        db.session.flush()
        db.session.get(Display, display_id).playlist_id = playlist.id
        db.session.commit()
        playlist_id = playlist.id
        broker = get_broker()
        sub = broker.subscribe(display_id)
        while not sub.queue.empty():  # drop the hint from the commit above
            sub.queue.get_nowait()

    data = _start(client)
    assert _approve(app, data["code"], name="ignored", replace_id=display_id) == display_id
    with app.app_context():
        assert get_broker().close(display_id) == 1  # what the admin route does after commit
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    assert ("revoked", {}) in events

    # The old credential stops working as soon as the approval commits.
    resp = client.get(f"{API}/manifest", headers=bearer(old_token))
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthorized"

    token = _status(client, data["pairing_secret"]).get_json()["token"]
    with app.app_context():
        assert Display.query.count() == 1
        display = db.session.get(Display, display_id)
        assert (display.name, display.playlist_id) == ("Lobby", playlist_id)
        assert display.token_hash == hash_token(token)
    manifest = client.get(f"{API}/manifest", headers=bearer(token)).get_json()
    assert manifest["display"] == {"id": display_id, "name": "Lobby"}
    assert manifest["playlist"]["id"] == playlist_id


def test_claim_is_single_use_even_if_display_deleted(app, configured, client):
    data = _start(client)
    display_id = _approve(app, data["code"])
    with app.app_context():
        db.session.delete(db.session.get(Display, display_id))
        db.session.commit()
        assert PairingCode.query.count() == 0  # FK cascade removed the code
    assert _status(client, data["pairing_secret"]).status_code == 404


# --- ADMIN ROUTE ------------------------------------------------------------------------
# These depend on POST /displays/pair (displays/routes.py, written by another agent).
# Form fields per the spec: code, name, playlist (0 = none), pair_as (0 = new display).

def _admin_pair(client, code, name="Lobby", playlist=0, pair_as=0):
    return client.post("/displays/pair", data={
        "code": code, "name": name, "playlist": str(playlist), "pair_as": str(pair_as),
    })


def test_full_bearer_flow_via_admin_route(app, admin_client):
    player = app.test_client()
    data = _start(player)
    assert _status(player, data["pairing_secret"]).get_json()["status"] == "pending"

    resp = _admin_pair(admin_client, data["code"].lower(), name="Lobby")  # no dash, lower
    assert resp.status_code == 302
    with app.app_context():
        display = Display.query.one()
        assert resp.headers["Location"].rstrip("/").endswith(f"/displays/{display.id}")

    body = _status(player, data["pairing_secret"]).get_json()
    assert body["status"] == "paired"
    with app.app_context():
        assert Display.query.one().token_hash == _sha(body["token"])
    assert _status(player, data["pairing_secret"]).status_code == 404


def test_expired_code_generic_flash_via_admin_route(app, admin_client):
    data = _start(app.test_client())
    _expire(app, data["code"])
    resp = _admin_pair(admin_client, data["code"])
    assert resp.status_code == 200
    assert b"That code is not valid or has expired." in resp.data
    with app.app_context():
        assert Display.query.count() == 0


def test_viewer_cannot_pair_via_admin_route(app, client, configured):
    data = _start(app.test_client())
    make_user(app, "val", "viewer")
    login(client, "val")
    assert _admin_pair(client, data["code"]).status_code == 403
    with app.app_context():
        assert Display.query.count() == 0
        assert PairingCode.query.one().display_id is None


def test_pair_as_existing_revokes_streams_via_admin_route(app, admin_client, paired_display):
    display_id, old_token = paired_display
    with app.app_context():
        sub = get_broker().subscribe(display_id)
    data = _start(app.test_client())
    resp = _admin_pair(admin_client, data["code"], name="", pair_as=display_id)
    assert resp.status_code == 302
    events = []
    while not sub.queue.empty():
        events.append(sub.queue.get_nowait())
    assert ("revoked", {}) in events
    with app.app_context():
        assert not get_broker().connected(display_id)
        display = Display.query.one()
        assert (display.id, display.name, display.token_hash) == (display_id, "Lobby", None)
    assert app.test_client().get(f"{API}/manifest", headers=bearer(old_token)).status_code == 401
