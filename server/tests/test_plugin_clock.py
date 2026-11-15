"""P1 clock plugin: plugin.json, config validation, items and the manifest entry, the runner
files, and the player-side code run in Node (tests/js/plugin_clock.js)."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aisignx.extensions import db
from aisignx.models import Media, Setting
from aisignx.plugins import registry as registry_mod
from aisignx.plugins import schema
from aisignx.plugins.registry import load_plugin

from .conftest import bearer, make_user
from .plugin_fixtures import make_plugin_app, paired_with

SERVER = Path(__file__).resolve().parent.parent
BUNDLED = SERVER / "aisignx" / "plugins" / "bundled" / "clock"
STATIC_ROOT = SERVER / "aisignx" / "static" / "player" / "plugins"
STATIC = STATIC_ROOT / "clock"
THEMES = ["minimal-dark", "minimal-light", "analog-classic", "flip-clock", "word-clock"]
API = "/api/player/v1"


@pytest.fixture(scope="module")
def plugin():
    return load_plugin(BUNDLED, STATIC_ROOT, bundled=True)


@pytest.fixture
def capp(tmp_path, monkeypatch):
    """An app whose registry holds only the bundled clock, so these tests do not depend on
    the other bundled plugins (they are built and tested in their own packages)."""
    original = registry_mod._plugin_folders

    def only_clock(root):
        folders = original(root)
        if Path(root) == registry_mod.BUNDLED_DIR:
            folders = [f for f in folders if f.name == "clock"]
        return folders

    monkeypatch.setattr(registry_mod, "_plugin_folders", only_clock)
    app = make_plugin_app(tmp_path, monkeypatch, PLUGIN_DIRS=[])
    yield app
    with app.app_context():
        db.engine.dispose()


def _login(app, client, role="editor"):
    from .conftest import login

    make_user(app, "admin", "admin")
    if role != "admin":
        make_user(app, "ed", role)
    login(client, "ed" if role != "admin" else "admin")


FORM = {"name": "Lobby clock", "duration": "", "theme": "flip-clock", "format": "12h",
        "seconds": "on", "show_date": "on", "date_style": "short", "timezone": "Asia/Tokyo",
        "label": "  Tokyo office  "}


# --- plugin.json --------------------------------------------------------------------------

def test_plugin_json_loads(plugin):
    raw = (BUNDLED / "plugin.json").read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert plugin.key == "clock" and plugin.render == "frame"
    assert plugin.data is None and plugin.secrets == () and not plugin.ends_itself
    assert plugin.default_duration == 10
    assert plugin.player_script is None and plugin.player_style is None
    for name in ("normalize", "fetch", "manifest_extra", "playable"):
        assert plugin.hook(name) is None  # everything happens on the screen


def test_schema_fields_and_defaults(plugin):
    fields = {f["name"]: f for f in plugin.schema}
    assert list(fields) == ["theme", "format", "seconds", "show_date", "date_style", "timezone",
                            "label"]
    assert [o["value"] for o in fields["theme"]["options"]] == THEMES
    assert [o["value"] for o in fields["format"]["options"]] == ["24h", "12h"]
    assert [o["value"] for o in fields["date_style"]["options"]] == ["long", "short"]
    assert fields["timezone"]["type"] == "timezone" and fields["date_style"]["show_if"]
    assert not any(f["private"] for f in plugin.schema)
    assert schema.defaults(plugin.schema) == {
        "theme": "minimal-dark", "format": "24h", "seconds": True, "show_date": True,
        "date_style": "long", "timezone": "", "label": ""}


def test_validation(plugin):
    ok, errors = schema.validate(plugin.schema, {"theme": "word-clock", "format": "12h",
                                                 "seconds": "off", "timezone": "Europe/Oslo",
                                                 "label": " Oslo ", "junk": "x"})
    assert not errors
    assert ok["theme"] == "word-clock" and ok["seconds"] is False and ok["show_date"] is False
    assert ok["label"] == "Oslo" and ok["timezone"] == "Europe/Oslo" and "junk" not in ok
    for bad in ({"theme": "sundial"}, {"format": "36h"}, {"date_style": "medium"},
                {"timezone": "Mars/Olympus"}, {"label": "x" * 61}):
        _, errors = schema.validate(plugin.schema, bad)
        assert set(errors) == set(bad), bad


# --- runner files -------------------------------------------------------------------------

def test_files_are_listed_and_loaded(plugin):
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script src="([^"]+)"></script>', html)
    styles = re.findall(r'<link rel="stylesheet" href="([^"]+)">', html)
    assert scripts[0] == "../sdk.js" and scripts[1] == "clock.js" and scripts[-1] == "main.js"
    assert styles == ["../sdk.css", "style.css"]
    local = [u for u in scripts + styles if not u.startswith("../")]
    assert sorted(local + ["index.html"]) == sorted(plugin.files)
    # Every theme file is loaded statically (never at runtime).
    on_disk = sorted(p.relative_to(STATIC).as_posix() for p in STATIC.rglob("*") if p.is_file())
    assert on_disk == sorted(plugin.files)


@pytest.mark.parametrize("path", sorted(p for p in STATIC.rglob("*") if p.is_file()),
                         ids=lambda p: p.relative_to(STATIC).as_posix())
def test_code_rules(path):
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    src = raw.decode("utf-8")
    assert len(src.splitlines()) < 800
    if path.suffix != ".js":
        return
    code = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    code = re.sub(r"(^|\s)//.*$", "", code, flags=re.M)
    assert "innerHTML" not in code and "outerHTML" not in code
    assert "insertAdjacentHTML" not in code and "document.write" not in code
    assert not re.search(r"\beval\s*\(|new Function\b", code)
    assert not re.search(r"""setAttribute\(\s*["']style["']""", code)
    assert not re.search(r"\b(setTimeout|setInterval)\s*\(", code)  # only ctx.later
    assert not re.search(r"\b(fetch|XMLHttpRequest|WebSocket|localStorage)\b", code)


def test_css_works_in_both_orientations():
    css = (STATIC / "style.css").read_text(encoding="utf-8")
    assert "@media (orientation: portrait)" in css and "@media (orientation: landscape)" in css
    assert "url(" not in css and "@import" not in css  # nothing loaded from outside


# --- items and the manifest ---------------------------------------------------------------

def test_create_from_the_form(capp):
    client = capp.test_client()
    _login(capp, client)
    page = client.get("/plugins/new/clock")
    assert page.status_code == 200 and b'name="theme"' in page.data
    resp = client.post("/plugins/new/clock", data=FORM)
    assert resp.status_code == 303
    with capp.app_context():
        media = Media.query.filter_by(media_type="plugin").one()
        assert media.plugin_key == "clock" and media.default_duration == 10
        assert media.plugin_config == {
            "theme": "flip-clock", "format": "12h", "seconds": True, "show_date": True,
            "date_style": "short", "timezone": "Asia/Tokyo", "label": "Tokyo office"}
        media_id = media.id
    assert client.get(f"/plugins/items/{media_id}/edit").status_code == 200
    preview = client.get(f"/plugins/items/{media_id}/preview")
    assert preview.status_code == 200 and b"clock" in preview.data


def test_bad_form_is_refused(capp):
    client = capp.test_client()
    _login(capp, client)
    resp = client.post("/plugins/new/clock", data={**FORM, "timezone": "Nowhere/Land"})
    assert resp.status_code == 400 and b"Unknown time zone" in resp.data
    with capp.app_context():
        assert Media.query.filter_by(media_type="plugin").count() == 0


def _create(app, config):
    from aisignx.plugins import service

    with app.test_request_context():
        plugin = service.get_plugin("clock")
        cfg, errors = service.validate_config(plugin, config)
        assert not errors, errors
        media = service.create_item(plugin, "Clock", None, cfg, None)
        db.session.commit()
        return media.id


def test_manifest_entry(capp):
    client = capp.test_client()
    make_user(capp, "admin", "admin")
    with capp.app_context():
        Setting.set("timezone", "America/Chicago")
        db.session.commit()
    org = _create(capp, {"theme": "analog-classic"})
    own = _create(capp, {"theme": "word-clock", "timezone": "Europe/Oslo"})
    token = paired_with(capp, org, own)
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    assert resp.status_code == 200
    first, second = resp.get_json()["items"]
    assert first["type"] == "plugin" and first["duration_ms"] == 10000
    assert re.fullmatch(r"/static/player/plugins/clock/index\.html#c=[0-9a-f]{12}", first["url"])
    block = first["plugin"]
    assert block["key"] == "clock" and block["render"] == "frame"
    assert block["data"] is None and block["refresh_s"] is None and not block["ends_itself"]
    assert block["config"]["timezone"] == "America/Chicago"  # "" means the org time zone
    assert block["config"]["theme"] == "analog-classic"
    assert second["plugin"]["config"]["timezone"] == "Europe/Oslo"
    assert first["url"] != second["url"]
    # No data endpoint: a clock never waits for the network, so it works offline.
    assert client.get(f"{API}/plugins/{org}/data", headers=bearer(token)).status_code == 404


def test_disabled_clock_leaves_the_manifest(capp):
    client = capp.test_client()
    make_user(capp, "admin", "admin")
    media_id = _create(capp, {})
    token = paired_with(capp, media_id)
    with capp.app_context():
        Setting.set("plugins.clock.enabled", "0")
        db.session.commit()
    resp = client.get(f"{API}/manifest", headers=bearer(token))
    assert resp.get_json()["items"] == []


# --- player-side code in Node -------------------------------------------------------------

@pytest.fixture(scope="module")
def js():
    if shutil.which("node") is None:
        pytest.skip("needs Node.js")
    out = subprocess.run(["node", str(SERVER / "tests" / "js" / "plugin_clock.js"), str(STATIC)],
                         capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def test_js_read(js):
    r = js["read"]
    assert r["h24"]["time"] == "14:52:37" and r["h24"]["ampm"] == ""
    assert r["h24"]["date"] == "Saturday, November 14, 2026"
    assert r["h12"]["time"] == "2:52:37" and r["h12"]["ampm"] == "PM"
    assert r["noSeconds"] == "14:52" and r["noDate"] == "" and r["short"] == "Sat, Nov 14"
    assert r["tokyo"]["time"] == "22:52:37"
    assert r["newYork"]["time"] == "8:52:37" and r["newYork"]["ampm"] == "AM"
    assert r["midnight12"]["hh"] == "12" and r["midnight12"]["ampm"] == "AM"
    assert r["midnight12"]["dayKey"] == "2026-11-15"  # the date follows the time zone
    assert r["noon12"] == "12:00:00"
    assert r["badZone"] is True  # an unknown zone falls back instead of throwing
    assert r["german"] == "Samstag, 14. November 2026"


def test_js_angles(js):
    a = js["angles"]
    assert a["three"] == {"hour": 90, "minute": 0, "second": 0}
    assert a["halfPast"] == {"hour": 15.25, "minute": 183, "second": 180}
    assert a["last"]["second"] == 354 and a["last"]["hour"] < 360


def test_js_word_clock(js):
    w = js["words"]
    assert w["14:52"]["text"] == "IT IS TEN TO THREE" and w["14:52"]["dots"] == 2
    assert w["09:00"]["text"] == "IT IS NINE O'CLOCK" and w["09:00"]["dots"] == 0
    assert w["23:37"]["text"] == "IT IS TWENTY-FIVE TO TWELVE"
    assert w["00:15"]["text"] == "IT IS QUARTER PAST TWELVE"
    assert w["12:30"]["text"] == "IT IS HALF PAST TWELVE"
    assert w["11:58"]["text"] == "IT IS FIVE TO TWELVE" and w["11:58"]["dots"] == 3
    assert w["14:52pm"][-1] == "PM" and w["09:00am"][-1] == "AM"
    assert js["grid"] == {"rows": 10, "widths": [11], "bad": []}
    assert js["wordsAllDay"] is None
    assert len(js["lit"]) == 2 + 2 + 6


def test_js_themes_mount_and_tick(js):
    assert js["themes"] == sorted(THEMES)
    m = js["mount"]
    for key, r in m.items():
        assert r["label"] == "Lobby" and r["date"] == "Saturday, November 14, 2026", key
        assert r["rewrites"] == 0, key  # an unchanged second touches no text
    assert m["minimal-dark/24h"]["main"] == "14:52" and m["minimal-dark/24h"]["ampmHidden"]
    assert m["minimal-light/12h"]["main"] == "2:52" and m["minimal-light/12h"]["ampm"] == "PM"
    analog = m["analog-classic/24h"]
    assert analog["svgNs"] == "http://www.w3.org/2000/svg" and analog["marks"] == 60
    assert analog["numbers"] == ",".join(str(n) for n in range(1, 13))
    assert analog["hands"] == ["rotate(86.31 100 100)", "rotate(315.70 100 100)",
                               "rotate(222.00 100 100)"]
    assert m["analog-classic/12h"]["digital"] == "2:52:37 PM"
    assert m["flip-clock/24h"]["digits"] == "145237" and m["flip-clock/24h"]["blank"] == 0
    assert m["flip-clock/12h"]["digits"] == "25237" and m["flip-clock/12h"]["blank"] == 1
    assert m["flip-clock/24h"]["flipped"] == 3  # 14:52:37 -> 14:53:00
    assert m["word-clock/24h"]["lit"] == "ITISTENTOTHREE" and m["word-clock/24h"]["dots"] == 2
    assert m["word-clock/12h"]["lit"] == "ITISPMTENTOTHREE"
    assert m["word-clock/24h"]["letters"] == 110


def test_js_main_ticks_on_boundaries(js):
    s = js["main"]["seconds"]
    assert s["body"] == "clock theme-minimal-dark with-seconds" and s["ready"] == 1
    assert s["waits"] == [615, 1000, 1000]  # 400 ms into a second: next tick just after it
    assert s["pending"] == s["afterShow"] == s["afterResume"] == 1
    assert s["resizeListener"] is True
    mins = js["main"]["minutes"]
    assert mins["body"] == "clock theme-analog-classic h12"
    assert mins["waits"] == [22615, 60000, 60000] and mins["time"] == "2:55 PM"
    # An unknown theme or an empty config still shows a clock.
    for name in ("unknownTheme", "emptyConfig"):
        assert js["main"][name]["body"] == "clock theme-minimal-dark with-seconds"
        assert js["main"][name]["ready"] == 1
