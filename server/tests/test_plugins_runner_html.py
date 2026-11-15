"""Frame plugin runner pages (static/player/plugins/<key>/index.html), the plugin file lists
on the player page, and the code rules of the player-side plugin scripts."""
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from aisignx.player.routes import PLUGINS_EXT

STATIC = Path(__file__).resolve().parent.parent / "aisignx" / "static"
PLUGINS = STATIC / "player" / "plugins"
RUNNER_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
              "media-src blob:; font-src 'self'; connect-src 'none'; frame-src 'none'; "
              "base-uri 'none'; form-action 'none'")
GOOD_RUNNER = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">\n'
    f'<meta http-equiv="Content-Security-Policy" content="{RUNNER_CSP}">\n'
    '<meta name="referrer" content="no-referrer">\n'
    '<link rel="stylesheet" href="../sdk.css"><link rel="stylesheet" href="style.css">\n'
    '</head><body><div id="root"></div><script src="../sdk.js"></script>'
    '<script src="main.js"></script></body></html>\n')


def runner_problems(html: str) -> list[str]:
    """Rule violations of one runner page (an empty list means it is fine)."""
    problems = []
    metas = re.findall(r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"\s*/?>', html, re.I)
    if metas != [RUNNER_CSP]:
        problems.append(f"CSP meta must be exactly the runner policy, found {metas}")
    if not re.search(r'<meta\s+name="referrer"\s+content="no-referrer"\s*/?>', html, re.I):
        problems.append("no referrer meta")
    for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S | re.I):
        if "src=" not in attrs or body.strip():
            problems.append("inline <script>")
    if re.search(r"<style\b", html, re.I):
        problems.append("inline <style>")
    if re.search(r"\son\w+\s*=", html, re.I):
        problems.append("on* attribute")
    if re.search(r"\sstyle\s*=", html, re.I):
        problems.append("style attribute")
    urls = re.findall(r'\s(?:src|href)\s*=\s*"([^"]*)"', html, re.I)
    for url in urls:
        if re.match(r"^(?:[a-z][a-z0-9+.-]*:|/)", url, re.I) or "?" in url or "#" in url:
            problems.append(f"URL must be relative without a query: {url}")
    scripts = re.findall(r'<script\b[^>]*\ssrc="([^"]*)"', html, re.I)
    if not scripts or scripts[0] != "../sdk.js":
        problems.append("../sdk.js must be the first script")
    if '"../sdk.css"' not in html:
        problems.append("../sdk.css missing")
    if '<div id="root"></div>' not in html:
        problems.append('no <div id="root">')
    return problems


def test_good_runner_passes_and_bad_ones_fail():
    assert runner_problems(GOOD_RUNNER) == []
    bad = {
        "csp": GOOD_RUNNER.replace("connect-src 'none'", "connect-src 'self'"),
        "inline": GOOD_RUNNER.replace("</body>", "<script>alert(1)</script></body>"),
        "style": GOOD_RUNNER.replace("</head>", "<style>body{}</style></head>"),
        "onload": GOOD_RUNNER.replace('<div id="root">', '<div id="root" onclick="x()">'),
        "query": GOOD_RUNNER.replace('src="main.js"', 'src="main.js?v=1"'),
        "absolute": GOOD_RUNNER.replace('src="main.js"', 'src="/static/player/plugins/x/main.js"'),
        "remote": GOOD_RUNNER.replace('src="main.js"', 'src="https://cdn.example/main.js"'),
    }
    for name, html in bad.items():
        assert runner_problems(html), name


def test_readme_runner_template_passes():
    # Plugin authors copy this block, so it must follow the rules exactly.
    readme = (PLUGINS / "README.md").read_text(encoding="utf-8")
    block = re.search(r"```html\n(.*?)```", readme, re.S)
    assert block and runner_problems(block.group(1)) == []
    assert block.group(1) == GOOD_RUNNER


def _runners() -> list[Path]:
    return sorted(PLUGINS.glob("*/index.html"))


@pytest.mark.parametrize("runner", _runners(), ids=lambda p: p.parent.name)
def test_runner_pages(runner):
    raw = runner.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "UTF-8 without a byte-order mark"
    html = raw.decode("utf-8")
    assert runner_problems(html) == []
    for url in re.findall(r'\s(?:src|href)\s*=\s*"([^"]*)"', html):
        assert (runner.parent / url).resolve().is_file(), url


def test_runners_match_the_registry(app):
    registry = app.extensions.get(PLUGINS_EXT)
    if registry is None:
        pytest.skip("plugin registry not installed")
    frame = {p.key for p in registry.all() if p.render == "frame" and p.bundled}
    on_disk = {p.parent.name for p in _runners()}
    assert frame <= on_disk
    for plugin in registry.all():
        if plugin.render != "frame":
            continue
        html = (plugin.static_dir / "index.html").read_text(encoding="utf-8")
        for url in re.findall(r'\s(?:src|href)\s*=\s*"([^"]*)"', html):
            if not url.startswith("../"):
                assert url in plugin.files, f"{plugin.key}: {url} is not listed in plugin.json files"


# --- the player page ------------------------------------------------------------------------

def _page(client):
    resp = client.get("/player/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    tpl = re.search(r'<template id="plugin-files">(.*?)</template>', html, re.S)
    assert tpl, "no plugin-files template"
    listed = re.findall(r'<link rel="prefetch" href="([^"]+)">', tpl.group(1))
    scripts = re.findall(r'<script src="(/static/player/plugins/[^"]+)"></script>', html)
    head = html.split("</head>")[0]
    styles = re.findall(r'<link rel="stylesheet" href="(/static/player/plugins/[^"]+)">', head)
    return html, listed, scripts, styles


class FakeRegistry:
    def __init__(self, plugins):
        self.plugins = plugins

    def all(self):
        return list(self.plugins)


def _plugin(key, render, files=(), script=None, style=None):
    return SimpleNamespace(key=key, render=render, files=tuple(files), player_script=script,
                           player_style=style, bundled=True)


def test_page_lists_every_installed_plugin(app, client, monkeypatch):
    fake = FakeRegistry([
        _plugin("zeta", "frame", ["index.html", "main.js", "style.css"]),
        _plugin("alpha", "native", ["native.js", "native.css", "extra.png"], "native.js", "native.css"),
        _plugin("beta", "native", [], "native.js"),
    ])
    monkeypatch.setitem(app.extensions, PLUGINS_EXT, fake)
    html, listed, scripts, styles = _page(client)
    base = "/static/player/plugins/"
    # Runner files without a query string (what the frames load), the SDK first.
    assert listed == [base + "sdk.js", base + "sdk.css", base + "alpha/extra.png",
                      base + "zeta/index.html", base + "zeta/main.js", base + "zeta/style.css"]
    # Native files are real tags with the build query, like the other player files.
    assert [s.split("?")[0] for s in scripts] == [base + "host.js", base + "alpha/native.js",
                                                  base + "beta/native.js"]
    assert all(re.search(r"\?v=[^\"&]+$", s) for s in scripts)
    assert [s.split("?")[0] for s in styles] == [base + "alpha/native.css"]
    assert all("?v=" in s for s in styles)
    # Native scripts load after render.js and the host, before player.js.
    order = re.findall(r'<script src="/static/player/([\w/]+)\.js', html)
    assert order.index("render") < order.index("plugins/host") < order.index("plugins/alpha/native")
    assert order.index("plugins/beta/native") < order.index("player")
    assert "<style" not in html and "style=" not in html


def test_page_without_plugins_lists_the_sdk(app, client, monkeypatch):
    monkeypatch.setitem(app.extensions, PLUGINS_EXT, FakeRegistry([]))
    _, listed, scripts, styles = _page(client)
    assert listed == ["/static/player/plugins/sdk.js", "/static/player/plugins/sdk.css"]
    assert [s.split("?")[0] for s in scripts] == ["/static/player/plugins/host.js"]
    assert styles == []


def test_every_listed_file_exists(app, client):
    # A 404 here would stop the Android shell from ever promoting a new page.
    _, listed, scripts, styles = _page(client)
    registry = app.extensions.get(PLUGINS_EXT)
    dirs = {p.key: p.static_dir for p in registry.all()} if registry is not None else {}
    for url in listed + [s.split("?")[0] for s in scripts + styles]:
        rel = url.removeprefix("/static/")
        parts = rel.split("/")
        if len(parts) > 3 and parts[2] in dirs:  # player/plugins/<key>/<file>
            path = Path(dirs[parts[2]]) / "/".join(parts[3:])
        else:
            path = STATIC / rel
        assert path.is_file(), url
    if registry is not None:
        expected = set()
        for p in registry.all():
            expected.update(f"player/plugins/{p.key}/{name}" for name in p.files)
            expected.update(f"player/plugins/{p.key}/{name}"
                            for name in (p.player_script, p.player_style) if name)
        shown = {u.split("?")[0].removeprefix("/static/") for u in listed + scripts + styles}
        assert expected <= shown


def test_enabling_a_plugin_does_not_change_the_page(app, client, monkeypatch):
    fake = FakeRegistry([_plugin("zeta", "frame", ["index.html"])])
    monkeypatch.setitem(app.extensions, PLUGINS_EXT, fake)
    first = _page(client)[1:]
    fake.plugins[0].enabled = False  # the page never reads the enabled flag
    assert _page(client)[1:] == first


# --- code rules for the player-side plugin scripts ----------------------------------------

@pytest.mark.parametrize("path", [
    PLUGINS / "host.js", PLUGINS / "sdk.js", STATIC / "js" / "plugin_preview.js",
    STATIC / "player" / "render.js",
], ids=lambda p: p.name)
def test_script_rules(path):
    src = path.read_text(encoding="utf-8")
    assert not path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert len(src.splitlines()) < 800
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)  # comments may name what the code avoids
    src = re.sub(r"(^|\s)//.*$", "", src, flags=re.M)
    assert "innerHTML" not in src and "outerHTML" not in src and "insertAdjacentHTML" not in src
    assert not re.search(r"\beval\s*\(|new Function\b", src)
    assert not re.search(r"""setAttribute\(\s*["']style["']""", src)


def test_sdk_files_exist():
    for name in ("host.js", "sdk.js", "sdk.css", "README.md"):
        assert (PLUGINS / name).is_file(), name
