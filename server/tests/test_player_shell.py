"""Phase 5 server side: /healthz JSON for client setup and the player shell hook."""
from pathlib import Path

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
