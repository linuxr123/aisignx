"""static/player/plugins/host.js and sdk.js, run in Node (tests/js/plugin_host.js):
handshake, data brokering, last good copy, assets, lifecycle messages."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
JS = ROOT / "js"
PLUGINS = ROOT.parent / "aisignx" / "static" / "player" / "plugins"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")


@pytest.fixture(scope="module")
def r():
    out = subprocess.run(["node", str(JS / "plugin_host.js"), str(PLUGINS)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_handshake_once_from_the_frame_and_an_opaque_origin(r):
    h = r["handshake"]
    assert h["foreign"] == 0      # another window's hello
    assert h["badOrigin"] == 0    # the frame, but not an opaque origin
    assert h["badVersion"] == 0
    assert h["accepted"] == 1
    assert h["second"] == 1       # a later hello is ignored
    assert h["readyViaWindow"] == "pending"  # after the handshake only the port counts
    assert h["readyViaPort"] == "resolved"
    init = h["init"]
    assert init["t"] == "init" and init["key"] == "echo" and init["config"] == {"word": "hi"}
    assert set(init) >= {"data", "stale", "warming", "fetched_at_ms", "offline", "server_offset_ms",
                         "tz", "locale", "duration_ms", "attribution"}
    assert init["duration_ms"] == 15000


def test_ready_timeout_and_fatal_error(r):
    assert r["readyTimeout"] == {"before": "pending", "after": "rejected", "error": "plugin not ready"}
    assert r["fatalBeforeReady"] == {"state": "rejected", "error": "plugin error: boom"}


def test_warming_then_data(r):
    w = r["warming"]
    assert w["init"] == {"t": "init", "data": None, "warming": True}
    assert w["at"] == [0, 7000]  # retried after Retry-After
    assert w["data"]["t"] == "data" and w["data"]["data"] == {"n": 1}
    assert w["data"]["warming"] is False


def test_retry_after_is_clamped(r):
    assert r["clampLow"] == [0, 5000, 10000]
    assert r["clampHigh"] == [0, 60000, 120000]


def test_refresh_only_while_shown(r):
    c = r["cadence"]
    assert c["hidden"] == 1
    assert c["shown"] == [0, 200000, 260000, 320000, 380000]  # at once (overdue), then every refresh_s
    assert c["inm"][0] is None and set(c["inm"][1:]) == {'"e0"'}
    assert c["msgs"] == ["init", "show"]  # a 304 sends nothing


def test_last_good_copy_lru(r):
    lru = r["lru"]
    assert lru["afterEight"] == [f"aisignx.plugin.{i}" for i in range(2, 9)]  # 1 evicted
    assert lru["total"] <= 1536 * 1024
    # An entry over 256 KB is not kept (and drops the old one); persist: false writes nothing.
    assert lru["afterHuge"] == [f"aisignx.plugin.{i}" for i in range(2, 8)]


def test_offline_init(r):
    o = r["offline"]
    assert o["saved"] == {"t": "init", "data": {"temp": 21}, "offline": True, "warming": False}
    assert o["inm"] == '"t"'
    assert o["otherConfig"]["data"] is None  # the copy belongs to another config
    assert o["early"] == 0                   # init waits for the first answer...
    assert o["hung"] == {"t": "init", "data": None, "offline": True, "warming": True}  # ...2.5 s at most


@pytest.mark.parametrize("name,complete", [("lifecycle", False), ("lifecycleEnds", True)])
def test_lifecycle_messages(r, name, complete):
    lc = r[name]
    assert lc["msgs"] == ["init", "show", "pause", "resume", "net:true", "net:false", "destroy"]
    expected = (["complete"] if complete else []) + ["error:soft:false", "degraded"]
    assert lc["events"] == expected  # complete only with ends_itself; nothing after destroy
    assert lc["degraded"] is True
    assert lc["closed"] is True      # destroy closes the port


def test_end_to_end_with_sdk(r):
    e = r["e2e"]
    assert e["readyState"] == "resolved"
    names = [x[0] for x in e["rec"]]
    assert names == ["init", "show", "pause", "resume", "net", "destroy"]
    init = e["rec"][0][1]
    assert init["data"] == {"word": "hello"} and init["config"] == {"word": "hi"}
    assert init["offset"] == 5000  # ctx.now() follows the server clock
    assert e["url"] == "blob:" and e["same"] is True
    assert e["unknown"] == "rejected:asset unavailable"  # not in the data's asset list
    assert e["assetFetches"] == ["/api/player/v1/plugins/7/assets/" + "a" * 32]
    assert e["ticksBefore"] == 2 and e["ticksAfter"] == 2  # destroy stops ctx.every timers


def test_sdk_fallback_is_ready_and_degraded(r):
    assert r["fallback"] == {"state": "resolved", "degraded": True, "card": ["aisx-fallback"]}


def test_shell_warm_up(r):
    assert r["warm"]["shell"] == ["/static/player/plugins/sdk.js",
                                  "/static/player/plugins/echo/index.html"]
    assert r["warm"]["browser"] == []


# --- render.js plugin path (tests/js/plugin_render.js) --------------------------------------

@pytest.fixture(scope="module")
def rr():
    out = subprocess.run(["node", str(JS / "plugin_render.js"), str(PLUGINS.parent)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_render_frame_plugin(rr):
    f = rr["frame"]
    assert f["attrs"]["sandbox"] == "allow-scripts"  # exactly: an opaque origin
    assert f["attrs"]["referrerpolicy"] == "no-referrer" and f["attrs"]["allow"] == ""
    assert f["className"] == "media plugin" and f["tabIndex"] == -1
    assert f["srcAtCreate"] == ""  # the host knows the frame before it can say hello
    assert f["src"].startswith("/static/player/plugins/echo/index.html#c=")
    assert f["readyMs"] == 8000 and f["isPlugin"] is True
    assert f["calls"] == ["show", "pause", "resume"]
    assert f["got"] == ["note:plugin echo: soft", "fail:plugin error: hard"]  # complete ignored
    assert f["afterTeardown"] == "destroy" and f["blank"] == "about:blank"


def test_render_complete_and_degraded(rr):
    assert rr["endsItself"] == ["done@0"]
    assert rr["degradedBusy"] == ["done@10000"]  # once, at 10 s, in a busy playlist
    assert rr["degradedAlone"] == []             # a single item keeps showing its card


def test_render_native_plugin(rr):
    n = rr["native"]
    assert n["unsupported"] == "rejected:unsupported plugin nope"
    assert n["hang"] == "rejected:plugin timed out"
    assert n["log"] == ["prepare", "run", "volume:0.5", "muted:true", "muted:false", "pause",
                        "resume", "teardown"]
    assert n["got"] == []  # done() without ends_itself is ignored
    assert len(n["endsGot"]) == 1
