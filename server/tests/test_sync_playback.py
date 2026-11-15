"""Synchronized playback: the group option, the manifest's sync block, diagnostics."""
import json

import pytest
from sqlalchemy import update

from aisignx.extensions import db
from aisignx.models import Display, DisplayGroup, Media, Playlist, PlaylistItem
from aisignx.player import diag

from .conftest import DISPLAY_TOKEN, bearer, login, make_user

API = "/api/player/v1"


def _setup(app, paired_display, group_sync=True, durations=(4, 6, 5)):
    """A paired display in a group, playing a playlist of web items with the given durations."""
    with app.app_context():
        group = DisplayGroup(name="Lobby", sync_playback=group_sync)
        db.session.add(group)
        playlist = Playlist(name="P")
        for pos, seconds in enumerate(durations):
            media = Media(name=f"Page {pos}", media_type="web", url=f"https://example.com/{pos}",
                          web_mode="live", default_duration=seconds)
            playlist.items.append(PlaylistItem(media=media, position=pos, duration=seconds))
        db.session.add(playlist)
        db.session.flush()
        display = db.session.get(Display, paired_display[0])
        display.group = group
        display.playlist_id = playlist.id
        display.allow_input = True
        db.session.commit()
        return group.id


def _manifest(client):
    return client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).get_json()


def test_synced_group_gets_a_sync_block_and_controls_off(app, configured, client, paired_display):
    gid = _setup(app, paired_display)
    m = _manifest(client)
    assert m["sync"] == {"group_id": gid, "anchor_ms": 0, "cycle_ms": 15000}
    assert m["settings"]["allow_input"] is False        # one screen going its own way would break it


def test_no_sync_block_without_the_group_option_or_a_group(app, configured, client, paired_display):
    _setup(app, paired_display, group_sync=False)
    m = _manifest(client)
    assert "sync" not in m and m["settings"]["allow_input"] is True
    with app.app_context():
        db.session.get(Display, paired_display[0]).group_id = None
        db.session.commit()
    assert "sync" not in _manifest(client)


def test_cycle_is_the_sum_of_the_item_durations_never_under_one_second(app, configured, client, paired_display):
    _setup(app, paired_display, durations=(1, 2, 3))
    assert _manifest(client)["sync"]["cycle_ms"] == 6000
    with app.app_context():  # a sub-second duration is shown for a second, as the player does
        for item in PlaylistItem.query.all():
            item.media.default_duration = 1
            item.duration = None
        db.session.commit()
    assert _manifest(client)["sync"]["cycle_ms"] == 3000


def test_every_display_of_the_group_gets_the_same_timeline(app, configured, client, paired_display):
    _setup(app, paired_display)
    with app.app_context():
        first = db.session.get(Display, paired_display[0])
        other = Display(name="Second", group_id=first.group_id, playlist_id=first.playlist_id,
                        token_hash="x" * 64)
        db.session.add(other)
        db.session.commit()
        from aisignx.player.manifest import build_manifest
        from flask import current_app
        with current_app.test_request_context():
            a, b = build_manifest(first), build_manifest(other)
    assert a["sync"] == b["sync"] and a["version"] != b["version"]  # (display id differs) but one timeline


def test_toggling_the_option_changes_the_manifest_version(app, configured, client, paired_display):
    _setup(app, paired_display, group_sync=False)
    off = _manifest(client)["version"]
    with app.app_context():
        DisplayGroup.query.one().sync_playback = True
        db.session.commit()
    assert _manifest(client)["version"] != off


def test_admin_sets_the_option_on_the_group_form(app, configured, client):
    login(client, "admin")
    r = client.post("/groups/new", data={"name": "Wall", "description": "", "sync_playback": "y"})
    assert r.status_code == 302
    with app.app_context():
        group = DisplayGroup.query.one()
        assert group.sync_playback is True
        gid = group.id
    assert "Synced" in client.get("/groups/").get_data(as_text=True)
    assert "checked" in client.get(f"/groups/{gid}/edit").get_data(as_text=True)
    client.post(f"/groups/{gid}/edit", data={"name": "Wall", "description": ""})
    with app.app_context():
        assert DisplayGroup.query.one().sync_playback is False


def test_diag_keeps_the_clock_offset_signed_and_clamped():
    data = json.loads(diag.clean({"sync": {"offset_ms": -2873.6, "rtt_ms": 41, "x": 1}}))
    assert data["sync"] == {"offset_ms": -2873, "rtt_ms": 41}
    assert json.loads(diag.clean({"sync": {"offset_ms": 10**12}}))["sync"]["offset_ms"] == 86_400_000
    assert diag.clean({"sync": "nope"}) == "{}"


def test_display_page_shows_the_group_sync_and_clock(app, configured, paired_display):
    _setup(app, paired_display)
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    with app.app_context():
        db.session.execute(update(Display).where(Display.id == paired_display[0]).values(
            diagnostics=json.dumps({"sync": {"offset_ms": -12, "rtt_ms": 8}}), diagnostics_at=db.func.now()))
        db.session.commit()
    page = c.get(f"/displays/{paired_display[0]}").get_data(as_text=True)
    assert "Synced playback" in page and "-12 ms from the server" in page and "round trip 8 ms" in page
