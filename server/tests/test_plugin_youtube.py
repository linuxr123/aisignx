"""YouTube plugin (P6): link parsing, embed URLs, the save-time check, the manifest entry, and
static/player/plugins/youtube/native.js run in Node (tests/js/plugin_youtube.js)."""
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from aisignx.extensions import db
from aisignx.models import Media
from aisignx.plugins import registry
from aisignx.plugins.bundled.youtube import urls

from .conftest import bearer, login, make_user
from .plugin_fixtures import add_item, make_plugin_app, paired_with

ROOT = Path(__file__).resolve().parent
NATIVE = ROOT.parent / "aisignx" / "static" / "player" / "plugins" / "youtube" / "native.js"
API = "/api/player/v1"
VID, VID2, VID3 = "dQw4w9WgXcQ", "9bZkp7q19f0", "kJQP7kiw5Fk"
LIST = "PLFgquLnL59alCl_2TQvOiD5Vgm1hCaGSI"
CHANNEL = "UC4R8DWoMoI7CAwX8_LjQHig"


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Load only the YouTube bundled plugin, so this module does not depend on the others.
    folders = registry._plugin_folders
    monkeypatch.setattr(registry, "_plugin_folders", lambda root: [
        p for p in folders(root) if root != registry.BUNDLED_DIR or p.name == "youtube"])
    app = make_plugin_app(tmp_path, monkeypatch)
    yield app
    with app.app_context():
        db.engine.dispose()


@pytest.fixture
def client(app):
    return app.test_client()


def _qs(url):
    parts = urlsplit(url)
    return parts, {k: v[0] for k, v in parse_qs(parts.query).items()}


def _build(**config):
    return urls.build({"source": "videos", "mute": True, "play_to_end": True, **config})


# --- plugin.json --------------------------------------------------------------------------

def test_plugin_json_loads(app):
    plugin = app.extensions["aisignx_plugins"].get("youtube")
    assert plugin.render == "native" and plugin.player_script == "native.js"
    assert plugin.player_style is None and plugin.data is None and plugin.ends_itself is True
    assert {f["name"] for f in plugin.schema} == {"source", "videos", "playlist", "live",
                                                  "shuffle", "play_to_end", "captions"}
    assert plugin.audio is True
    defaults = {f["name"]: f.get("default") for f in plugin.schema}
    assert defaults["play_to_end"] is True
    assert defaults["source"] == "videos" and plugin.terms
    raw = (registry.BUNDLED_DIR / "youtube" / "plugin.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert NATIVE.is_file() and not NATIVE.read_bytes().startswith(b"\xef\xbb\xbf")


# --- link forms ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    (VID, (VID, None, None)),
    (f"https://www.youtube.com/watch?v={VID}", (VID, None, None)),
    (f"https://youtube.com/watch?feature=share&v={VID}&t=1m30s", (VID, 90, None)),
    (f"https://m.youtube.com/watch?v={VID}&t=45", (VID, 45, None)),
    (f"https://music.youtube.com/watch?v={VID}", (VID, None, None)),
    (f"https://youtu.be/{VID}?t=90", (VID, 90, None)),
    (f"youtu.be/{VID}", (VID, None, None)),
    (f"www.youtube.com/watch?v={VID}", (VID, None, None)),
    (f"https://www.youtube.com/shorts/{VID}", (VID, None, None)),
    (f"https://www.youtube.com/live/{VID}?si=abc", (VID, None, None)),
    (f"https://www.youtube.com/embed/{VID}?start=10&end=20", (VID, 10, 20)),
    (f"https://www.youtube-nocookie.com/embed/{VID}", (VID, None, None)),
    (f"https://www.youtube.com/watch?v={VID}&list={LIST}", (VID, None, None)),
    (f"https://www.youtube.com/watch?v={VID}&t=garbled", (VID, None, None)),
])
def test_accepted_video_forms(text, expected):
    assert urls.parse_video(text) == expected


@pytest.mark.parametrize("text", [
    f"https://vimeo.com/{VID}",
    f"https://www.youtube.com.evil.example/watch?v={VID}",
    f"https://evil.example/?u=https://youtu.be/{VID}",
    f"https://user:pw@www.youtube.com/watch?v={VID}",
    f"https://www.youtube.com:8443/watch?v={VID}",
    f"ftp://www.youtube.com/watch?v={VID}",
    f"javascript:alert('{VID}')",
    "https://www.youtube.com/watch?v=short",
    "https://www.youtube.com/channel/UCxxxx",
    f"https://www.youtube.com/embed/videoseries?list={LIST}",
    "not a video", "",
])
def test_rejected_video_forms(text):
    with pytest.raises(urls.UrlError):
        urls.parse_video(text)


def test_parse_time():
    assert [urls.parse_time(t) for t in ("90", "90s", "1:30", "1:02:03", "1m30s", "1h2m3s", "0")] \
        == [90, 90, 90, 3723, 90, 3723, 0]
    for bad in ("", "1:61", "abc", "-5", "25h"):
        with pytest.raises(urls.UrlError):
            urls.parse_time(bad)


def test_parse_videos_lines():
    text = f"# lobby loop\n\n{VID} 0:30 1:45\nhttps://youtu.be/{VID2}\n{VID3} 10\n"
    assert urls.parse_videos(text) == [(VID, 30, 105), (VID2, None, None), (VID3, 10, None)]
    with pytest.raises(urls.UrlError, match="Line 1: the end must come after the start"):
        urls.parse_videos(f"{VID} 60 30")
    with pytest.raises(urls.UrlError, match="Line 2"):
        urls.parse_videos(f"{VID}\nhttps://vimeo.com/1")
    with pytest.raises(urls.UrlError, match="at least one"):
        urls.parse_videos("\n# only a comment\n")
    with pytest.raises(urls.UrlError, match="At most 50"):
        urls.parse_videos("\n".join([VID] * 51))
    assert len(urls.parse_videos("\n".join([VID] * 50))) == 50


def test_parse_playlist_and_live():
    assert urls.parse_playlist(LIST) == LIST
    assert urls.parse_playlist(f"https://www.youtube.com/playlist?list={LIST}") == LIST
    assert urls.parse_playlist(f"https://youtube.com/watch?v={VID}&list={LIST}") == LIST
    for bad in ("", VID, f"https://www.youtube.com/watch?v={VID}", f"https://evil.example/?list={LIST}"):
        with pytest.raises(urls.UrlError):
            urls.parse_playlist(bad)
    assert urls.parse_live(CHANNEL) == ("channel", CHANNEL)
    assert urls.parse_live(f"https://www.youtube.com/channel/{CHANNEL}/live") == ("channel", CHANNEL)
    assert urls.parse_live(VID) == ("video", VID)
    assert urls.parse_live(f"https://www.youtube.com/live/{VID}") == ("video", VID)
    with pytest.raises(urls.UrlError, match="channel id"):
        urls.parse_live("https://www.youtube.com/@somechannel")
    with pytest.raises(urls.UrlError):
        urls.parse_live("https://www.youtube.com/channel/UCshort")


# --- embed URL ----------------------------------------------------------------------------

def test_embed_one_video():
    built = _build(videos=f"{VID} 30 90")
    parts, q = _qs(built["url"])
    assert f"{parts.scheme}://{parts.netloc}" == urls.EMBED_ORIGIN
    assert parts.path == f"/embed/{VID}" and not parts.fragment
    assert q == {"autoplay": "1", "mute": "1", "playsinline": "1", "controls": "0", "rel": "0",
                 "iv_load_policy": "3", "disablekb": "1", "fs": "0", "cc_load_policy": "0",
                 "start": "30", "end": "90"}
    assert built["embed"] == {"video_ids": [VID], "list": None, "channel": None,
                              "shuffle": False, "captions": False, "play_to_end": True,
                              "live": False, "start": 30, "end": 90}
    _, q = _qs(_build(videos=VID, play_to_end=False, mute=False)["url"])
    assert q["loop"] == "1" and q["playlist"] == VID and q["mute"] == "0"
    for dropped in ("modestbranding", "vq", "playback_rate", "enablejsapi", "origin"):
        assert dropped not in q


def test_embed_several_videos_and_shuffle():
    built = _build(videos=f"{VID} 5\n{VID2} 7\n{VID3}", shuffle=True)
    parts, q = _qs(built["url"])
    assert parts.path == f"/embed/{VID}" and q["playlist"] == f"{VID2},{VID3}"
    assert q["start"] == "5" and "loop" not in q  # start/end: first video only
    assert built["embed"]["video_ids"] == [VID, VID2, VID3] and built["embed"]["shuffle"]
    _, q = _qs(_build(videos=f"{VID}\n{VID2}", play_to_end=False)["url"])
    assert q["loop"] == "1"


def test_embed_playlist_live_and_captions():
    parts, q = _qs(_build(source="playlist", playlist=LIST, shuffle=True)["url"])
    assert parts.path == "/embed/videoseries" and q["list"] == LIST and "loop" not in q
    _, q = _qs(_build(source="playlist", playlist=LIST, play_to_end=False)["url"])
    assert q["loop"] == "1"
    live = _build(source="live", live=f"https://www.youtube.com/live/{VID}", shuffle=True)
    assert urlsplit(live["url"]).path == f"/embed/{VID}"
    assert live["embed"]["live"] and not live["embed"]["play_to_end"]
    assert not live["embed"]["shuffle"]
    chan = _build(source="live", live=CHANNEL)
    parts, q = _qs(chan["url"])
    assert parts.path == "/embed/live_stream" and q["channel"] == CHANNEL
    assert chan["embed"]["channel"] == CHANNEL
    _, q = _qs(urls.build({"source": "videos", "videos": VID, "captions": True}, "nl-NL")["url"])
    assert q["cc_load_policy"] == "1" and q["cc_lang_pref"] == "nl" and q["hl"] == "nl"
    _, q = _qs(urls.build({"source": "videos", "videos": VID, "captions": True}, "x")["url"])
    assert "cc_lang_pref" not in q


# --- items, form and manifest -------------------------------------------------------------

def _validate(app, raw):
    from aisignx.plugins import service

    with app.test_request_context():
        return service.validate_config(service.get_plugin("youtube"), raw)


def test_normalize_keeps_what_was_typed_and_flags_the_field(app):
    typed = f"https://youtu.be/{VID} 0:30\n{VID2}"
    config, errors = _validate(app, {"source": "videos", "videos": typed})
    assert not errors and config["videos"] == typed
    again, errors = _validate(app, config)  # idempotent
    assert not errors and again == config
    _, errors = _validate(app, {"source": "videos", "videos": "https://vimeo.com/123"})
    assert "videos" in errors and "YouTube" in errors["videos"]
    _, errors = _validate(app, {"source": "playlist", "playlist": "nope"})
    assert "playlist" in errors
    _, errors = _validate(app, {"source": "live", "live": ""})
    assert "live" in errors


def test_editor_creates_from_the_form(app, client):
    make_user(app, "admin", "admin")
    make_user(app, "ed", "editor")
    login(client, "ed")
    page = client.get("/plugins/new/youtube")
    assert page.status_code == 200 and b'name="videos"' in page.data
    bad = client.post("/plugins/new/youtube", data={"name": "Promo", "duration": "",
                                                    "source": "videos", "videos": "vimeo.com/1"})
    assert bad.status_code == 400
    resp = client.post("/plugins/new/youtube", data={
        "name": "Promo", "duration": "", "source": "videos", "videos": f"youtu.be/{VID}",
        "playlist": "", "live": "", "mute": "1", "play_to_end": "1"})
    assert resp.status_code == 303
    with app.app_context():
        media = Media.query.filter_by(plugin_key="youtube").one()
        assert media.default_duration == 300 and media.plugin_config["videos"] == f"youtu.be/{VID}"


def test_manifest_entry(app, client):
    make_user(app, "admin", "admin")
    media_id = add_item(app, "youtube", {"source": "videos", "videos": f"{VID}\n{VID2}",
                                         "mute": False, "shuffle": True})
    token = paired_with(app, media_id)
    with app.app_context():  # sound is the playlist item's setting, not the plugin's
        from aisignx.models import PlaylistItem
        pi = PlaylistItem.query.one()
        assert pi.mute is True and pi.volume == 100
        pi.mute, pi.volume = False, 40
        db.session.commit()
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    item, = resp.get_json()["items"]
    assert item["type"] == "plugin" and item["muted"] is False and item["volume"] == 40
    url, digest = item["url"].split("#c=")
    assert url.startswith(f"{urls.EMBED_ORIGIN}/embed/{VID}?autoplay=1&mute=0") and len(digest) == 12
    plugin = item["plugin"]
    assert plugin["render"] == "native" and plugin["data"] is None and plugin["ends_itself"] is True
    assert plugin["embed"]["video_ids"] == [VID, VID2] and plugin["embed"]["shuffle"] is True
    page = client.get("/player/")
    assert b"player/plugins/youtube/native.js?v=" in page.data
    login(client, "admin")
    preview = client.get(f"/plugins/items/{media_id}/preview")
    assert preview.status_code == 200 and b"player/plugins/youtube/native.js" in preview.data


def test_broken_stored_config_is_not_played(app, client):
    make_user(app, "admin", "admin")
    media_id = add_item(app, "youtube", {"source": "videos", "videos": VID})
    with app.app_context():
        media = db.session.get(Media, media_id)
        media.plugin_config = {**media.plugin_config, "videos": "https://vimeo.com/1"}
        db.session.commit()
    token = paired_with(app, media_id)
    assert client.get(f"{API}/manifest", headers=bearer(token)).get_json()["items"] == []


# --- native.js ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def js():
    if shutil.which("node") is None:
        pytest.skip("needs Node.js")
    out = subprocess.run(["node", str(ROOT / "js" / "plugin_youtube.js"), str(NATIVE)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_build_src(js):
    u = js["util"]
    _, q = _qs(u["plain"])
    assert q["autoplay"] == "0" and q["enablejsapi"] == "1"  # prepared hidden; run() plays it
    assert q["origin"] == "http://signage.local:5000" and "#" not in u["plain"]
    assert "origin" not in _qs(u["noOrigin"])[1]
    parts, q = _qs(u["shuffled"])
    assert parts.path == "/embed/bbbbbbbbbbb" and q["playlist"] == "aaaaaaaaaaa,ccccccccccc"
    assert "start" not in q and "end" not in q  # they belonged to the first listed video
    assert u["rejected"] == [None] * 9
    assert u["shuffleKeepsAll"] == [1, 2, 3, 4, 5]


def test_js_message_filter(js):
    f = js["filter"]
    assert f["ok"] == {"event": "onReady"} and f["object"] == {"event": "onReady"}
    for name in ("foreignSource", "youtubeCom", "nullOrigin", "garbage", "noEvent", "huge"):
        assert f[name] is None, name


def test_js_frame_and_bridge(js):
    b = js["bridge"]
    assert b["attrs"] == {"sandbox": "allow-scripts allow-same-origin allow-presentation",
                          "allow": "autoplay; encrypted-media; picture-in-picture",
                          "referrerpolicy": "strict-origin-when-cross-origin",
                          "scrolling": "no", "title": "YouTube video"}
    assert b["tabIndex"] == -1 and b["attached"] and "media" in b["className"].split()
    assert b["listening"] >= 1 and b["subscribed"]
    assert b["targets"] == ["https://www.youtube-nocookie.com"]
    assert b["beforeReady"] == "pending" and b["prepared"] == "resolved"
    assert b["runCommands"] == ["mute", "setVolume", "playVideo"]
    assert b["controls"] == [["pauseVideo", []], ["playVideo", []], ["setVolume", [25]],
                             ["mute", []], ["mute", []]]  # the item itself is muted
    assert b["got"] == ["done@10"]  # only after the last of the three videos
    assert b["blank"] == "about:blank" and b["listeners"] == 0 and b["afterTeardown"] == 0


def test_js_endings(js):
    e = js["endings"]
    assert e["loopOff"] == [] and e["live"] == ["degraded"]
    assert e["single"] and e["single"][0].startswith("done@")
    assert len(e["reportedTwice"]) == 1  # infoDelivery and onStateChange both say "ended"
    assert e["listUnknownEnds"] == ["done@2500"] and e["listNextStarts"] == []
    assert e["errorAfterRun"] == ["fail:youtube error 150: embedding is turned off for this video"]
    assert e["unknownError"] == []


def test_js_failures_and_fallbacks(js):
    f = js["failures"]
    assert f["badUrl"].startswith("rejected:") and f["framesForBadUrl"] == 0
    assert f["errorBeforeReady"].startswith("rejected:youtube error 101")
    assert f["silentPrepared"] == "resolved"
    assert _qs(f["reloadSrc"])[1]["autoplay"] == "1" and "reloaded" in f["reloadNote"][0]
    assert f["autoMuted"]["mute"] and "playing muted" in f["autoMuted"]["note"][0]


def test_js_offline_card(js):
    o = js["offline"]
    assert o["res"] == "resolved" and o["frames"] == 0 and o["hostSaw"]
    assert o["cards"] == [["Video unavailable", "This screen is offline"]]
    assert o["got"] == ["degraded"]
    assert o["navigator"] == ["resolved", [["Video unavailable", "This screen is offline"]], 0]
