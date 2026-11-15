"""Phase 5 server side: /healthz JSON for client setup and the player shell hook."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

HEALTHZ = {"status": "ok", "app": "aisignx", "api": 1}
STATIC = Path(__file__).resolve().parents[1] / "aisignx" / "static" / "player"


def _no_cookie(resp):
    assert "Set-Cookie" not in resp.headers


def test_healthz_before_setup(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.is_json
    assert resp.json == HEALTHZ
    _no_cookie(resp)


def test_healthz_after_setup_needs_no_login(configured, client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json == HEALTHZ
    _no_cookie(resp)


def test_healthz_discloses_no_version(client):
    from aisignx import __version__

    assert __version__ not in client.get("/healthz").get_data(as_text=True)


def test_player_page_headers_unchanged(configured, client):
    resp = client.get("/player/")
    assert resp.status_code == 200
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert "no-store" in resp.headers["Cache-Control"]


def test_player_page_serves_net_and_player_scripts(configured, client):
    html = client.get("/player/").get_data(as_text=True)
    assert "player/net.js" in html and "player/player.js" in html


def test_static_scripts_have_shell_hook_and_web_fallback():
    net = (STATIC / "net.js").read_text(encoding="utf-8")
    player = (STATIC / "player.js").read_text(encoding="utf-8")
    assert "AiSignXShell" in net and "AiSignXShellInfo" in net and "AiSignXShellPort" in net
    assert "shell: shell" in net
    assert "AiSignXShell" in player
    assert '"web-"' in player  # plain browsers still report web-<build>
    assert 'shell.onState("PAIRED")' in player
    assert "shell.onManifest(" in player


def test_static_scripts_served(configured, client):
    for name in ("net.js", "player.js"):
        resp = client.get(f"/static/player/{name}")
        assert resp.status_code == 200
        assert "AiSignXShell" in resp.get_data(as_text=True)
        resp.close()


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_shell_gets_full_manifest_once_per_page_load():
    """A cached, current manifest must not hide settings from a (new) shell behind a 304."""
    script = Path(__file__).resolve().parent / "js" / "player_shell_manifest.js"
    out = subprocess.run(["node", str(script), str(STATIC / "player.js")], capture_output=True,
                         text=True, timeout=30, check=True).stdout
    result = json.loads(out)
    shell = result["withShell"]
    assert shell["etags"] == [None, "v1"]  # first fetch skips the ETag, later ones use it
    assert len(shell["fed"]) == 1 and shell["fed"][0]["settings"]["unlock"] == {"hash": "h"}
    assert result["browser"]["etags"] == ["v1", "v1"]  # plain browsers keep the 304 path


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_shell_screenshot_of_css_rotated_screen_is_turned_upright():
    script = Path(__file__).resolve().parent / "js" / "device_capture.js"
    out = subprocess.run(["node", str(script), str(STATIC / "device.js")], capture_output=True,
                         text=True, timeout=30, check=True).stdout
    r90, r180, r270, r0, native = json.loads(out)
    for result, deg in ((r90, 90), (r180, 180), (r270, 270)):
        assert result["blob"] == "rotated"
        [canvas] = result["drawn"]
        assert ["rotate", -deg] in canvas["ops"]  # counter-clockwise by the CSS rotation
        sideways = deg in (90, 270)
        assert (canvas["w"], canvas["h"]) == ((1080, 1920) if sideways else (1920, 1080))
    # Unrotated screens and shells that rotate the device natively upload the capture as is.
    assert r0 == {"blob": "raw", "drawn": []} and native == {"blob": "raw", "drawn": []}


@pytest.mark.skipif(shutil.which("node") is None, reason="needs Node.js")
def test_player_asks_its_browser_which_videos_it_can_decode():
    script = Path(__file__).resolve().parent / "js" / "device_support.js"
    out = json.loads(subprocess.run(["node", str(script), str(STATIC / "device.js")], capture_output=True,
                                    text=True, timeout=30, check=True).stdout)
    assert out["report"] == {"checked": 3, "problems": [{"id": 1, "s": "no"}, {"id": 2, "s": "rough"}]}
    assert out["asked"][0] == ["file", 'video/mp4; codecs="avc1.640033"', 3840, 25, 40000000]
    assert len(out["asked"]) == 3 and out["askedAgain"] == 3   # one question per video, none repeated
    assert out["afterChange"] == {"checked": 1, "problems": []}
    assert out["noApi"]["diag"] is True and out["throws"] is True
