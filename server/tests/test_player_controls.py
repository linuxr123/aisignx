"""Player controls in the browser player: input mapping, pause/skip/seek mechanics, PoP pauses."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "aisignx" / "static" / "player"
JS = Path(__file__).resolve().parent / "js"
needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


def _node(script, arg):
    out = subprocess.run(["node", str(JS / script), str(arg)], capture_output=True,
                         encoding="utf-8", timeout=60, check=True).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def harness():
    return _node("controls_harness.js", STATIC)


def test_player_page_has_control_elements_and_script_order(configured, client):
    html = client.get("/player/").get_data(as_text=True)
    assert '<div id="controls" hidden></div>' in html
    assert 'id="paused"' in html and 'class="p-sub"' in html and 'id="hint"' in html
    order = re.findall(r"player/(\w+)\.js", html)
    assert order == ["net", "sync", "render", "emergency", "device", "pop", "controls", "player"]


def test_controls_script_served_and_es2018():
    src = (STATIC / "controls.js").read_text(encoding="utf-8")
    assert "innerHTML" not in src and "?." not in src and "??" not in src
    assert "A.controls" in src
    assert len(src.splitlines()) < 800


@needs_node
def test_key_mapping(harness):
    keys = harness["keys"]
    expected = {
        " ": "toggle", "Enter": "toggle", "ArrowRight": "forward", "ArrowLeft": "back",
        "ArrowUp": "prev", "ArrowDown": "next", "PageUp": "prev", "PageDown": "next",
        "MediaPlayPause": "toggle", "MediaPlay": "play", "MediaPause": "pause", "MediaStop": "pause",
        "MediaTrackNext": "next", "MediaTrackPrevious": "prev", "MediaFastForward": "forward",
        "MediaRewind": "back", "ChannelUp": "prev", "ChannelDown": "next",
    }
    for key, action in expected.items():
        assert keys[key] == {"action": action, "prevented": True}, key
    assert keys["a"] == {"action": None, "prevented": False}


@needs_node
def test_key_repeat_and_cross_source_dedupe(harness):
    assert harness["repeatForward"] == 3  # 0, 250, 500 ms within 500 ms of repeats
    assert harness["repeatNext"] == 1     # repeats of other actions are ignored
    assert harness["dedupe"] == ["toggle", "pause"]  # the echo within 150 ms is dropped


@needs_node
def test_gestures(harness):
    g = harness["gestures"]
    assert g["tap"] == ["toggle"]
    assert g["swipeLeft"] == ["next"] and g["swipeRight"] == ["prev"]
    assert g["vertical"] == [] and g["shortMove"] == [] and g["longPress"] == []
    assert g["holdDragImage"] == ["prev"]  # a hold-drag on an image acts as a swipe
    assert g["clickDelayed"] == {"at100": 0, "after": ["toggle"]}
    assert g["dblclick"] == ["forward", "back"]  # right half, then left half; no click toggles
    assert g["wheel"] == {"actions": ["forward", "forward", "back"], "prevented": True}
    assert g["sideButtons"] == {"actions": ["prev", "next"], "prevented": True}
    assert harness["rotated90"] == ["prev", "next"]


@needs_node
def test_controls_ignored_when_disabled(harness):
    assert harness["ignoredInputOff"] == {"paused": False, "overlayHidden": True}
    assert harness["overlayShownPlaying"] is True
    assert harness["ignoredEmergency"] is False
    assert harness["ignoredNotPlaying"] == {"paused": False, "enabled": False, "overlayHidden": True}


@needs_node
def test_pause_freezes_deadline_and_resumes(harness):
    p = harness["pause"]
    assert p["pausedNow"] and p["pillShown"] and p["sub"] == "resumes in 5:00"
    assert p["first"] == p["stillFirst"] == p["beforeEnd"] == 1  # 3 s + pause + 6.5 s < 10 s
    assert p["after"] == 2
    assert p["pop"][-2:] == ["begin:1", "pause"]
    assert p["hint"] == "Playing"
    a = harness["autoResume"]
    assert a == {"at59": True, "at60": False, "cur": 1, "pillHidden": True}
    assert harness["emergencyResumes"] == {"paused": False, "pop": ["pause", "resume"]}


@needs_node
def test_skips(harness):
    assert harness["skips"] == [1, 2, 1, 4]  # next, prev, prev wraps to the last
    assert harness["rapid3"] == 4
    assert harness["rapidPrev2"] == 2
    assert harness["singleRestart"] == {"newPlay": ["end", "begin:1"], "stillShown": 1, "swaps": 1}
    assert harness["singleVideoRestart"] == {"t": 0, "playing": True}
    assert harness["imageSeek"] == {"forward": 2, "back": 1, "hint": "Next"}


@needs_node
def test_video_seek_and_scrub(harness):
    assert harness["videoSeek"] == {"t": 12, "hint": "Fast forward +10 s", "at19": 1, "at21": 2}
    assert harness["videoBack"] == {"t": 0, "hint": "Rewind −4 s"}
    assert harness["loopSeek"] == {"at57": 1, "at61": 2}  # looping: deadline unchanged
    assert harness["forwardAtEnd"] == 2
    assert harness["pausedSeek"] == {"videoPausedWhilePaused": True, "at19": 1, "at21": 2}
    assert harness["scrub"] == {"t": 130, "playing": True, "hintScrub": True}
    r = harness["render"]
    assert r["a"] == {"applied": 0, "atEnd": True}
    assert r["b"] == {"applied": -50, "atEnd": False}
    assert r["c"] == {"applied": 0, "atEnd": True}
    assert r["d"] == 59.75
    assert r["img"] == {"applied": 0, "atEnd": False}


@needs_node
def test_shell_controls_and_commands(harness):
    s = harness["shell"]
    assert s["paused"] is True
    assert s["commands"] == [
        {"action": "unlock", "request_id": "0123456789abcdef", "minutes": 15},
        {"action": "lock", "request_id": "fedcba9876543210"},
    ]
    assert s["hintUnlock"] == "Unlocked for 15 min" and s["hintLock"] == "Locked"
    assert harness["browserUnlock"] == {"debugHint": "No app to unlock", "plainHintHidden": True}


@needs_node
def test_pop_excludes_paused_time():
    out = _node("pop_pause.js", STATIC / "pop.js")
    assert out["paused"] == [5000]
    assert out["endWhilePaused"] == [4000]
    assert out["pausedAndCovered"] == [3000]
    assert out["skips"] == [3000]


def test_net_adapter_has_control_hooks():
    net = (STATIC / "net.js").read_text(encoding="utf-8")
    assert 'controls: 1, "kiosk-lock": 1' in net
    assert '{ t: "controls" }' in net and 't: "command"' in net
    assert "onCommand:" in net and "onControl:" in net


@needs_node
def test_seeks_are_coalesced_while_the_video_is_still_seeking(harness):
    c = harness["coalesce"]
    assert c["r"] == [10, 10, 10, -5]          # each press still reports its own jump
    assert c["duringSeek"] == [110]            # one write; the rest wait for 'seeked'
    assert c["afterSeeked"] == [110, 125]      # then one write with the net result
    assert c["end"]["atEnd"] is True


@needs_node
def test_manual_slide_changes_move_left_and_right_short_videos_step_less_mouse_drag_scrubs(harness):
    # swaps in order: item 1 (start, fade), 2 (auto, its own zoom), 3 by hand next (slides left),
    # 2 by hand previous (slides right), 3 (auto, its own wipe).
    assert harness["transitions"][:5] == ["fade", "zoom", "slide-left", "slide-right", "wipe"]
    assert harness["shortStep"] == {"t": 4, "hint": "Fast forward +3 s"}
    assert harness["mouseDrag"] == {"moved": True, "playing": True}
    assert harness["mouseClick"] == {"paused": True}


@needs_node
def test_sync_position_math_and_the_clock_estimate(harness):
    u = harness["syncUnit"]
    assert u["first"] == {"index": 0, "elapsedMs": 0, "remainingMs": 4000, "cycleMs": 15000}
    assert u["mid"]["index"] == 1 and u["mid"]["elapsedMs"] == 5999 and u["mid"]["remainingMs"] == 1
    assert u["wrap"]["index"] == 1 and u["wrap"]["elapsedMs"] == 0     # three cycles on, 4 s in: the 2nd slide begins
    assert u["anchor"]["index"] == 1 and u["anchor"]["elapsedMs"] == 0  # (5000 - 1000) % 10000 = 4000
    assert u["before"]["index"] == 1                                    # a time before the anchor still maps in
    assert u["short"]["index"] == 0 and u["short"]["elapsedMs"] == 500  # slides are never shorter than 1 s
    assert u["empty"] is None and u["zero"] is None and u["bad"] is None
    assert u["slowOnly"] == 150 and u["best"] == 0 and u["rtt"] == 20   # the fast answer wins
    assert u["ready"] is True and u["now"] == 5000
    assert u["junk"] == [False, False, False]                           # NaN, negative and 20 s round trips are ignored


@needs_node
def test_synced_screens_with_wrong_clocks_show_the_same_slide(harness):
    r = harness["sync"]
    assert r["checked"] > 600
    # Clocks 3 s slow, 2 s fast and 0.4 s fast, one screen joining 7.3 s late: all agree with the
    # shared timeline (boundaries are skipped: a screen may be a tick early or late there).
    assert r["wrong"] == [0, 0, 0], r
    # A video joined 8 s into its 20 s slide starts about 8 s in (clock error aside).
    v = harness["syncVideo"]
    assert v["cur"] == 7 and 7 <= v["time"] <= 9.5, v
    assert harness["noSync"]["cur"] == 1  # without a sync block nothing changes: item 1 first


@needs_node
def test_failing_items_keep_the_current_slide(harness):
    r = harness["offlineKeeps"]
    assert r["stuck"]["cur"] == 1 and r["onlyWorking"] is True  # only the working slide played
    assert r["stuck"]["noContent"] is False                    # never a blank message
    assert {2, 3} <= set(r["after"])                           # the others play again once they load


def test_content_is_never_interactive_whatever_the_settings(configured, client):
    """Web pages, YouTube, plugins, videos and images never take clicks, taps or keys."""
    html = client.get("/player/").get_data(as_text=True)
    assert '<body class="no-input"' in html  # nothing is touchable before the settings arrive
    css = (STATIC / "player.css").read_text(encoding="utf-8")
    assert ".slide .media, .slide iframe" in css and "pointer-events: none !important" in css
    render = (STATIC / "render.js").read_text(encoding="utf-8")
    assert 'media.tagName === "IFRAME"' in render and "media.inert = true" in render
