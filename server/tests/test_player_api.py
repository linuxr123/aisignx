"""Player API: credentials, manifest, versions, heartbeat, media, errors, setup and CSRF."""
import json
import re

import pytest

from aisignx.extensions import db
from aisignx.media import service as media_service
from aisignx.media import storage
from aisignx.models import Display, EmergencyMessage, Media, Playlist, PlaylistItem, Schedule
from aisignx.player.auth import hash_token
from aisignx.timeutil import as_utc, utcnow

from .conftest import (
    DISPLAY_TOKEN, PASSWORD, bearer, csrf_token_from, make_csrf_app, make_user, png_bytes,
)

API = "/api/player/v1"
IMAGE_BYTES = png_bytes((32, 18))
VIDEO_BYTES = bytes(range(256)) * 64  # 16 KB of "video"; the server never decodes it
OLD_KEYS = {"v", "version", "server_time_ms", "heartbeat_s", "display", "playlist", "items"}
ITEM_KEYS = {"id", "media_id", "type", "url", "mime", "sha256", "size", "width", "height",
             "duration_ms", "media_duration_ms", "transition", "fit", "muted"}


def _write_media_file(app, name: str, data: bytes) -> None:
    with app.app_context():
        (storage.media_dir() / name).write_bytes(data)


@pytest.fixture
def content(app, configured, paired_display):
    """A playlist with an image, a video and a web page, assigned to the paired display.

    Items are inserted out of order so the manifest must sort by position.
    """
    display_id, token = paired_display
    image_name = "a" * 32 + ".png"
    video_name = "b" * 32 + ".mp4"
    _write_media_file(app, image_name, IMAGE_BYTES)
    _write_media_file(app, video_name, VIDEO_BYTES)
    with app.app_context():
        image = Media(name="Logo", media_type="image", original_filename="secret-logo.png",
                      stored_name=image_name, mime_type="image/png", file_size=len(IMAGE_BYTES),
                      checksum_sha256="1" * 64, width=32, height=18, default_duration=10)
        video = Media(name="Promo", media_type="video", original_filename="promo-final.mp4",
                      stored_name=video_name, mime_type="video/mp4", file_size=len(VIDEO_BYTES),
                      checksum_sha256="2" * 64, width=1920, height=1080,
                      duration_seconds=31.5, default_duration=10)
        web = Media(name="Menu", media_type="web", url="https://example.com/menu",
                    default_duration=20)
        playlist = Playlist(name="Morning", default_transition="fade")
        db.session.add_all([image, video, web, playlist])
        db.session.flush()
        web_item = PlaylistItem(playlist=playlist, media=web, position=2, transition="cut")
        image_item = PlaylistItem(playlist=playlist, media=image, position=0)
        video_item = PlaylistItem(playlist=playlist, media=video, position=1, duration=32,
                                  transition=None, fit_mode="fill", mute=False)
        db.session.add_all([web_item, image_item, video_item])
        db.session.get(Display, display_id).playlist_id = playlist.id
        db.session.commit()
        return {
            "display_id": display_id, "token": token, "playlist_id": playlist.id,
            "image": image.id, "video": video.id, "web": web.id,
            "image_name": image_name, "video_name": video_name,
            "items": [image_item.id, video_item.id, web_item.id],
        }


def _manifest(client, token=DISPLAY_TOKEN):
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    assert resp.status_code == 200, resp.get_json()
    return resp.get_json()


def _version(client, token=DISPLAY_TOKEN):
    return _manifest(client, token)["version"]


# --- credentials ------------------------------------------------------------------------

def test_manifest_requires_credential(app, configured, client, paired_display):
    resp = client.get(f"{API}/manifest")
    assert resp.status_code == 401
    assert resp.is_json
    assert resp.get_json()["error"] == "unauthorized"
    assert resp.headers["Cache-Control"] == "no-store"

    assert client.get(f"{API}/manifest", headers=bearer("wrong")).status_code == 401
    assert client.get(f"{API}/manifest",
                      headers={"Authorization": "Basic abc"}).status_code == 401
    # Tokens are never read from the query string.
    assert client.get(f"{API}/manifest?token={DISPLAY_TOKEN}").status_code == 401

    assert client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).status_code == 200
    assert client.get(f"{API}/manifest",
                      headers={"Authorization": f"bearer {DISPLAY_TOKEN}"}).status_code == 200

    cookie_client = app.test_client()
    cookie_client.set_cookie("aisignx_display", DISPLAY_TOKEN, path="/api/player")
    assert cookie_client.get(f"{API}/manifest").status_code == 200


def test_bearer_header_wins_over_cookie(app, configured, client, paired_display):
    client.set_cookie("aisignx_display", "stale-cookie", path="/api/player")
    assert client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).status_code == 200


def test_unpaired_or_deleted_display_gets_401_and_cookie_cleared(app, configured, client,
                                                                  paired_display):
    display_id, token = paired_display
    client.set_cookie("aisignx_display", token, path="/api/player")
    assert client.get(f"{API}/manifest").status_code == 200

    with app.app_context():
        display = db.session.get(Display, display_id)
        display.token_hash = None
        display.paired_at = None
        db.session.commit()
    resp = client.get(f"{API}/manifest")
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthorized"
    cleared = [c for c in resp.headers.getlist("Set-Cookie") if c.startswith("aisignx_display=")]
    assert len(cleared) == 1
    lowered = cleared[0].lower()
    assert "path=/api/player" in lowered
    assert "max-age=0" in lowered or "expires=thu, 01 jan 1970" in lowered
    assert client.get_cookie("aisignx_display", path="/api/player") is None

    # Deleted display: heartbeat and media are refused the same way.
    with app.app_context():
        display = db.session.get(Display, display_id)
        display.token_hash = hash_token(token)
        db.session.commit()
        db.session.delete(display)
        db.session.commit()
    for resp in (client.post(f"{API}/heartbeat", json={}, headers=bearer(token)),
                 client.get(f"{API}/events", headers=bearer(token)),
                 client.get(f"{API}/media/1/x.png", headers=bearer(token))):
        assert resp.status_code == 401
        assert resp.get_json()["error"] == "unauthorized"


# --- manifest ---------------------------------------------------------------------------

def test_manifest_shape(app, client, content):
    resp = client.get(f"{API}/manifest", headers=bearer(content["token"]))
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-cache"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    body = resp.get_json()
    assert set(body) == OLD_KEYS | {"source", "emergency"}
    assert body["v"] == 1
    assert len(body["version"]) == 16 and int(body["version"], 16) >= 0
    assert abs(body["server_time_ms"] - utcnow().timestamp() * 1000) < 60_000
    assert body["heartbeat_s"] == 30
    assert body["display"] == {"id": content["display_id"], "name": "Lobby"}
    assert body["playlist"] == {"id": content["playlist_id"], "name": "Morning"}
    assert body["source"] == {"type": "default", "id": None, "name": None}
    assert body["emergency"] is None

    items = body["items"]
    assert [i["id"] for i in items] == content["items"]  # position order
    assert all(set(i) == ITEM_KEYS for i in items)
    image, video, web = items

    assert image == {
        "id": content["items"][0], "media_id": content["image"], "type": "image",
        "url": f"{API}/media/{content['image']}/{content['image_name']}",
        "mime": "image/png", "sha256": "1" * 64, "size": len(IMAGE_BYTES),
        "width": 32, "height": 18, "duration_ms": 10_000, "media_duration_ms": None,
        "transition": "fade", "fit": "fit", "muted": True,
    }
    assert video["type"] == "video"
    assert video["url"] == f"{API}/media/{content['video']}/{content['video_name']}"
    assert video["duration_ms"] == 32_000  # the item override
    assert video["media_duration_ms"] == 31_500
    assert video["transition"] == "fade"  # inherited from the playlist
    assert (video["fit"], video["muted"]) == ("fill", False)
    assert web == {
        "id": content["items"][2], "media_id": content["web"], "type": "web",
        "url": "https://example.com/menu", "mime": None, "sha256": None, "size": None,
        "width": None, "height": None, "duration_ms": 20_000, "media_duration_ms": None,
        "transition": "cut", "fit": "fit", "muted": True,
    }

    for item in (image, video):
        assert item["url"].startswith("/")  # relative: safe behind a proxy
        assert "localhost" not in item["url"]
    raw = resp.get_data(as_text=True)
    for leaked in ("Logo", "Promo", "secret-logo", "promo-final", DISPLAY_TOKEN):
        assert leaked not in raw


def test_manifest_random_transition_and_video_default_duration(app, client, content):
    with app.app_context():
        playlist = db.session.get(Playlist, content["playlist_id"])
        playlist.default_transition = "random"
        db.session.get(PlaylistItem, content["items"][1]).duration = None
        db.session.commit()
    image, video, web = _manifest(client)["items"]
    assert image["transition"] == video["transition"] == "random"
    assert web["transition"] == "cut"
    assert video["duration_ms"] == 32_000  # ceil(31.5 s)


def test_manifest_skips_unplayable_items(app, client, content):
    with app.app_context():
        playlist = db.session.get(Playlist, content["playlist_id"])
        no_file = Media(name="Pending", media_type="image", stored_name=None)
        bad_web = Media(name="Bad", media_type="web", url="javascript:alert(1)")
        db.session.add_all([no_file, bad_web])
        db.session.flush()
        playlist.items.append(PlaylistItem(media=no_file, position=3))
        playlist.items.append(PlaylistItem(media=bad_web, position=4))
        db.session.commit()
    assert [i["id"] for i in _manifest(client)["items"]] == content["items"]


def test_display_without_playlist(app, configured, client, paired_display):
    body = _manifest(client)
    assert body["playlist"] is None
    assert body["items"] == []
    assert body["display"]["name"] == "Lobby"


def test_manifest_does_not_touch_last_seen(app, client, content):
    _manifest(client)
    with app.app_context():
        assert db.session.get(Display, content["display_id"]).last_seen_at is None


# --- versions ---------------------------------------------------------------------------

def test_etag_and_conditional_get(app, client, content):
    resp = client.get(f"{API}/manifest", headers=bearer(content["token"]))
    version = resp.get_json()["version"]
    assert resp.headers["ETag"] == f'"{version}"'
    assert _version(client) == version  # stable across calls

    headers = {**bearer(content["token"]), "If-None-Match": f'"{version}"'}
    not_modified = client.get(f"{API}/manifest", headers=headers)
    assert not_modified.status_code == 304
    assert not_modified.data == b""
    assert not_modified.headers["ETag"] == f'"{version}"'

    headers["If-None-Match"] = '"0000000000000000"'
    assert client.get(f"{API}/manifest", headers=headers).status_code == 200


def _item_change(attr, value, index=1):
    def change(content):
        setattr(db.session.get(PlaylistItem, content["items"][index]), attr, value)
    return change


def _reorder(content):
    first, second = (db.session.get(PlaylistItem, i) for i in content["items"][:2])
    first.position, second.position = second.position, first.position


def _media_default_duration(content):
    db.session.get(Media, content["image"]).default_duration = 15


def _reassign_playlist(content):
    other = Playlist(name="Evening")
    db.session.add(other)
    db.session.flush()
    db.session.get(Display, content["display_id"]).playlist_id = other.id


def _rename_display(content):
    db.session.get(Display, content["display_id"]).name = "Front desk"


@pytest.mark.parametrize("change", [
    _item_change("transition", "zoom"),
    _item_change("duration", 45),
    _item_change("fit_mode", "stretch"),
    _item_change("mute", True),
    _reorder,
    _media_default_duration,
    _reassign_playlist,
    _rename_display,
], ids=["transition", "duration", "fit", "mute", "reorder", "media-duration", "reassign",
        "rename"])
def test_version_changes_with_content(app, client, content, change):
    before = _version(client)
    with app.app_context():
        change(content)
        db.session.commit()
    assert _version(client) != before


def test_version_changes_on_media_delete(app, client, content):
    before = _version(client)
    with app.test_request_context():
        media_service.delete_media(db.session.get(Media, content["video"]))
    body = _manifest(client)
    assert body["version"] != before
    assert [i["id"] for i in body["items"]] == [content["items"][0], content["items"][2]]


def test_version_ignores_unrelated_changes(app, client, content):
    before = _version(client)
    with app.app_context():
        db.session.get(Media, content["image"]).name = "Renamed logo"  # not in the manifest
        db.session.commit()
    client.post(f"{API}/heartbeat", json={"client_id": "abc"}, headers=bearer(content["token"]))
    assert _version(client) == before


def test_deleting_playlist_clears_display_playlist(app, admin_client, content):
    assert admin_client.post(f"/playlists/{content['playlist_id']}/delete").status_code == 302
    with app.app_context():
        assert db.session.get(Display, content["display_id"]).playlist_id is None
    body = _manifest(app.test_client())
    assert body["playlist"] is None and body["items"] == []


# --- source and emergency (Phase 4, additive) --------------------------------------------

def _add_schedule(content, **kw) -> int:
    """An all-screens schedule playing a one-item web playlist "Lunch"."""
    lunch = Playlist(name="Lunch")
    lunch.items.append(PlaylistItem(position=0, media=Media(
        name="Soup", media_type="web", url="https://example.com/soup", default_duration=15)))
    schedule = Schedule(name="Lunch menu", playlist=lunch, scope="all", **kw)
    db.session.add(schedule)
    db.session.commit()
    return schedule.id


def _add_emergency(**kw) -> int:
    em = EmergencyMessage(title="Fire drill", message="Leave by the nearest exit.",
                          scope="all", target_label="All screens", **kw)
    db.session.add(em)
    db.session.commit()
    return em.id


def test_source_and_emergency_keys_without_playlist(app, configured, client, paired_display):
    body = _manifest(client)
    assert set(body) == OLD_KEYS | {"source", "emergency"}
    assert body["source"] == {"type": "none", "id": None, "name": None}
    assert body["emergency"] is None


def test_schedule_changes_source_playlist_and_version(app, client, content):
    before = _manifest(client)
    with app.app_context():
        schedule_id = _add_schedule(content)
    body = _manifest(client)
    assert body["version"] != before["version"]
    assert body["source"] == {"type": "schedule", "id": schedule_id, "name": "Lunch menu"}
    # Old clients just see another playlist, in the unchanged shape.
    assert body["playlist"]["name"] == "Lunch"
    assert [i["url"] for i in body["items"]] == ["https://example.com/soup"]
    assert all(set(i) == ITEM_KEYS for i in body["items"])
    assert body["display"] == before["display"]
    assert body["heartbeat_s"] == before["heartbeat_s"] and body["v"] == 1

    with app.app_context():
        db.session.get(Schedule, schedule_id).enabled = False
        db.session.commit()
    assert _manifest(client)["version"] == before["version"]  # back to the default


def test_text_emergency_keeps_existing_keys(app, client, content):
    before = _manifest(client)
    with app.app_context():
        em_id = _add_emergency(level="warning")
    body = _manifest(client)
    assert body["version"] != before["version"]
    for key in OLD_KEYS - {"version", "server_time_ms"}:
        assert body[key] == before[key], key  # an old client keeps playing its content
    assert body["source"] == before["source"]
    em = body["emergency"]
    assert set(em) == {"id", "title", "message", "level", "background", "foreground",
                       "layout", "since"}
    assert em["id"] == em_id and em["title"] == "Fire drill"
    assert em["message"] == "Leave by the nearest exit."
    assert (em["level"], em["background"], em["foreground"]) == ("warning", "#e65100", "#ffffff")
    assert em["layout"] == "fullscreen"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", em["since"])

    with app.app_context():
        db.session.get(EmergencyMessage, em_id).cleared_at = utcnow()
        db.session.commit()
    after = _manifest(client)
    assert after["emergency"] is None
    assert after["version"] == before["version"]


def test_emergency_playlist_replaces_items(app, client, content):
    with app.app_context():
        exit_list = Playlist(name="Exit")
        exit_list.items.append(PlaylistItem(position=0, media=Media(
            name="Map", media_type="web", url="https://example.com/exit", default_duration=5)))
        db.session.add(exit_list)
        db.session.flush()
        em_id = _add_emergency(playlist_id=exit_list.id)
    body = _manifest(client)
    assert body["source"] == {"type": "emergency", "id": em_id, "name": "Fire drill"}
    assert body["playlist"]["name"] == "Exit"
    assert [i["url"] for i in body["items"]] == ["https://example.com/exit"]
    assert (body["emergency"]["layout"], body["emergency"]["background"]) == ("banner",
                                                                             "#b71c1c")


def test_etag_304_with_schedule_and_emergency(app, client, content):
    with app.app_context():
        _add_schedule(content)
        em_id = _add_emergency()
    version = _version(client)
    headers = {**bearer(content["token"]), "If-None-Match": f'"{version}"'}
    resp = client.get(f"{API}/manifest", headers=headers)
    assert resp.status_code == 304 and resp.headers["ETag"] == f'"{version}"'

    with app.app_context():
        db.session.get(EmergencyMessage, em_id).cleared_at = utcnow()
        db.session.commit()
    resp = client.get(f"{API}/manifest", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["emergency"] is None
    # The heartbeat reports the same version as the manifest.
    beat = client.post(f"{API}/heartbeat", json={}, headers=bearer(content["token"]))
    assert beat.get_json()["manifest_version"] == resp.get_json()["version"]


# --- heartbeat --------------------------------------------------------------------------

def test_heartbeat_updates_display(app, client, content):
    version = _version(client)
    resp = client.post(f"{API}/heartbeat", headers=bearer(content["token"]),
                       environ_base={"REMOTE_ADDR": "192.0.2.44"}, json={
        "client_id": "0123abcdef", "player_version": "web-" + "9" * 60,
        "current_item_id": content["items"][1], "manifest_version": version,
        "screen_w": 1920, "screen_h": 99999, "unknown_key": {"ignored": True},
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["manifest_version"] == version
    assert body["heartbeat_s"] == 30
    assert abs(body["server_time_ms"] - utcnow().timestamp() * 1000) < 60_000
    assert resp.headers["Cache-Control"] == "no-store"

    with app.app_context():
        display = db.session.get(Display, content["display_id"])
        assert (utcnow() - as_utc(display.last_seen_at)).total_seconds() < 60
        assert display.last_ip == "192.0.2.44"
        assert display.client_id == "0123abcdef"
        assert display.player_version == ("web-" + "9" * 60)[:32]
        assert display.reported_version == version
        assert display.current_item_id == content["items"][1]
        assert (display.screen_width, display.screen_height) == (1920, 16384)


def test_heartbeat_ignores_bad_types_and_clamps(app, configured, client, paired_display):
    display_id, token = paired_display
    resp = client.post(f"{API}/heartbeat", headers=bearer(token), json={
        "player_version": 5, "manifest_version": ["x"], "current_item_id": "7",
        "screen_w": -20, "screen_h": True,
    })
    assert resp.status_code == 200
    with app.app_context():
        display = db.session.get(Display, display_id)
        assert display.player_version is None and display.reported_version is None
        assert display.current_item_id is None
        assert (display.screen_width, display.screen_height) == (0, None)


@pytest.mark.parametrize("payload", [
    {"client_id": "bad id!"}, {"client_id": "x" * 65}, {"client_id": 12},
])
def test_heartbeat_rejects_bad_client_id(app, configured, client, paired_display, payload):
    resp = client.post(f"{API}/heartbeat", headers=bearer(DISPLAY_TOKEN), json=payload)
    assert resp.status_code == 400
    assert resp.get_json()["error"] == "bad_request"


def test_heartbeat_rejects_non_object_and_oversized_bodies(app, configured, client,
                                                         paired_display):
    resp = client.post(f"{API}/heartbeat", headers=bearer(DISPLAY_TOKEN), data="[]",
                       content_type="application/json")
    assert resp.status_code == 400

    big = json.dumps({"client_id": "abc", "player_version": "x" * 9000})
    resp = client.post(f"{API}/heartbeat", headers=bearer(DISPLAY_TOKEN), data=big,
                       content_type="application/json")
    assert resp.status_code == 413
    assert resp.is_json
    assert resp.get_json()["error"] == "too_large"
    with app.app_context():
        assert db.session.get(Display, paired_display[0]).last_seen_at is None

    resp = client.post(f"{API}/pair", data=big, content_type="application/json")
    assert resp.status_code == 413 and resp.get_json()["error"] == "too_large"


# --- media ------------------------------------------------------------------------------

def _media_url(content, kind):
    return f"{API}/media/{content[kind]}/{content[kind + '_name']}"


def test_media_requires_credential(app, client, content):
    resp = client.get(_media_url(content, "image"))
    assert resp.status_code == 401
    assert resp.get_json()["error"] == "unauthorized"


def test_media_served_private_and_immutable(app, client, content):
    resp = client.get(_media_url(content, "image"), headers=bearer(content["token"]))
    assert resp.status_code == 200
    assert resp.mimetype == "image/png"
    assert resp.data == IMAGE_BYTES
    cache = resp.headers["Cache-Control"]
    assert "private" in cache and "immutable" in cache and "max-age=31536000" in cache
    assert "public" not in cache and "no-cache" not in cache
    assert resp.headers["X-Content-Type-Options"] == "nosniff"

    video = client.get(_media_url(content, "video"), headers=bearer(content["token"]))
    assert video.status_code == 200 and video.mimetype == "video/mp4"
    assert video.data == VIDEO_BYTES


def test_media_range_request(app, client, content):
    resp = client.get(_media_url(content, "video"),
                      headers={**bearer(content["token"]), "Range": "bytes=100-199"})
    assert resp.status_code == 206
    assert resp.data == VIDEO_BYTES[100:200]
    assert resp.headers["Content-Range"] == f"bytes 100-199/{len(VIDEO_BYTES)}"


def test_media_not_found_cases(app, client, content):
    headers = bearer(content["token"])
    wrong_name = f"{API}/media/{content['image']}/{content['video_name']}"
    for url in (wrong_name,
                f"{API}/media/{content['image']}/{'c' * 32}.png",
                f"{API}/media/9999/{content['image_name']}",
                f"{API}/media/{content['web']}/anything"):
        resp = client.get(url, headers=headers)
        assert resp.status_code == 404, url
        assert resp.get_json()["error"] == "not_found"


def test_media_missing_file_is_json_404(app, client, content):
    with app.app_context():
        (storage.media_dir() / content["image_name"]).unlink()
    resp = client.get(_media_url(content, "image"), headers=bearer(content["token"]))
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"


def test_admin_media_file_still_needs_login(app, client, content):
    assert client.get(f"/media/{content['image']}/file").status_code == 302


# --- setup, routing errors --------------------------------------------------------------

def test_before_setup(app, client):
    for method, url in (("get", f"{API}/manifest"), ("post", f"{API}/pair"),
                        ("post", f"{API}/heartbeat"), ("get", f"{API}/events")):
        resp = getattr(client, method)(url)
        assert resp.status_code == 503, url
        assert resp.get_json()["error"] == "setup_required"
        assert resp.headers["Retry-After"] == "30"

    assert client.get("/player/").status_code == 200

    resp = client.get(f"{API}/nope")
    assert resp.status_code == 404
    assert resp.get_json()["error"] == "not_found"

    for page in ("/", "/displays/", "/media/"):
        resp = client.get(page)
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/setup/")


def test_api_routing_errors_are_json(app, configured, client):
    resp = client.get(f"{API}/nope")
    assert resp.status_code == 404 and resp.get_json()["error"] == "not_found"
    resp = client.get("/api/elsewhere")
    assert resp.status_code == 404 and resp.is_json

    resp = client.get(f"{API}/pair")
    assert resp.status_code == 405
    assert resp.get_json()["error"] == "method_not_allowed"
    assert "POST" in resp.headers["Allow"]
    resp = client.delete(f"{API}/manifest")
    assert resp.status_code == 405 and resp.is_json

    # Admin pages keep their HTML error pages.
    page = client.get("/no-such-page")
    assert page.status_code == 404 and not page.is_json


# --- CSRF-enabled app -------------------------------------------------------------------

def test_player_api_is_csrf_exempt(tmp_path, monkeypatch):
    app = make_csrf_app(tmp_path, monkeypatch)
    make_user(app, "admin", "admin")
    with app.app_context():
        db.session.add(Display(name="Hall", token_hash=hash_token(DISPLAY_TOKEN)))
        db.session.commit()
    client = app.test_client()

    pair = client.post(f"{API}/pair", json={"client_id": "abc"})
    assert pair.status_code == 201
    status = client.post(f"{API}/pair/status",
                         json={"pairing_secret": pair.get_json()["pairing_secret"]})
    assert status.status_code == 200 and status.get_json()["status"] == "pending"
    assert client.post(f"{API}/heartbeat", json={"client_id": "abc"},
                       headers=bearer(DISPLAY_TOKEN)).status_code == 200

    # Admin forms keep CSRF (this part needs the admin route POST /displays/pair).
    token = csrf_token_from(client.get("/login").get_data(as_text=True))
    assert client.post("/login", data={"username": "admin", "password": PASSWORD,
                                       "csrf_token": token}).status_code == 302
    resp = client.post("/displays/pair", data={"code": pair.get_json()["code"],
                                               "name": "X", "playlist": "0", "pair_as": "0"})
    assert resp.status_code == 400
    with app.app_context():
        assert Display.query.count() == 1
        db.engine.dispose()


# --- /player/ page ----------------------------------------------------------------------

def test_player_page(app, configured, client):
    resp = client.get("/player/")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    csp = resp.headers["Content-Security-Policy"]
    for directive in ("default-src 'self'", "script-src 'self'", "style-src 'self'",
                      "frame-ancestors 'none'", "base-uri 'none'", "frame-src http: https:"):
        assert directive in csp
    assert "unsafe-inline" not in csp

    html = resp.get_data(as_text=True)
    assert "navbar" not in html
    assert f'data-api="{API}"' in html
    scripts = re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)
    names = [re.search(r'src="/static/player/(\w+)\.js\?v=[^"]+"', attrs).group(1)
             for attrs, _ in scripts]
    assert names == ["net", "render", "emergency", "player"]
    for _, body in scripts:
        assert body.strip() == ""  # no inline script
    assert re.search(r'<div id="emergency" hidden[ >]', html)  # overlay starts hidden
    assert "style=" not in html
    assert "<style" not in html
    # The page works without a credential and without login.
    assert app.test_client().get("/player/").status_code == 200


# --- proxy ------------------------------------------------------------------------------

def _heartbeat_ip(app) -> str:
    client = app.test_client()
    resp = client.post(f"{API}/heartbeat", json={}, headers={
        **bearer(DISPLAY_TOKEN), "X-Forwarded-For": "203.0.113.7"})
    assert resp.status_code == 200
    with app.app_context():
        return Display.query.one().last_ip


def test_forwarded_for_ignored_without_proxy_hops(app, configured, paired_display):
    assert _heartbeat_ip(app) == "127.0.0.1"


def test_forwarded_for_used_with_proxy_hops(tmp_path, monkeypatch):
    app = make_csrf_app(tmp_path, monkeypatch, PROXY_HOPS=1)
    make_user(app, "admin", "admin")
    with app.app_context():
        db.session.add(Display(name="Hall", token_hash=hash_token(DISPLAY_TOKEN)))
        db.session.commit()
    assert _heartbeat_ip(app) == "203.0.113.7"
    with app.app_context():
        db.engine.dispose()


def test_proxy_hops_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("AISIGNX_PROXY_HOPS", "1")
    app = make_csrf_app(tmp_path, monkeypatch)
    assert app.config["PROXY_HOPS"] == 1
    with app.app_context():
        db.engine.dispose()
