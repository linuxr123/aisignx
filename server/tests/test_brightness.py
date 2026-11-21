"""Brightness and night dimming: the setting, the manifest, the player's rule, bulk edit."""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from aisignx.displays import options
from aisignx.extensions import db
from aisignx.models import Display, Setting

from .conftest import login, make_user

needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
PLAYER = Path(__file__).resolve().parents[1] / "aisignx" / "static" / "player"


@pytest.fixture(scope="module")
def harness():
    script = Path(__file__).resolve().parent / "js" / "device_brightness.js"
    out = subprocess.run(["node", str(script), str(PLAYER / "device.js")], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_the_player_works_out_the_brightness(harness):
    assert harness["plain"] == [100, 55, 10, 100, 100]                         # clamped to 10..100, junk means 100
    assert harness["nightInside"] == 30 and harness["nightMorning"] == 30 and harness["nightOutside"] == 80
    assert harness["edgeFrom"] == [80, 30] and harness["edgeTo"] == [30, 80]    # the window starts at "from" and ends at "to"
    assert harness["daytimeWindow"] == 50
    assert harness["dimNeverBrighter"] == 20                                    # night never makes it brighter than the base level
    assert harness["badDim"] == [70, 70, 70]                                    # an unusable window is ignored


@needs_node
def test_the_dimmer_layer_is_made_only_when_needed_and_steps_aside_for_an_emergency(harness):
    assert harness["noLayerAt100"] == 0
    assert harness["layerOpacity"] == "0.4"
    assert harness["emergencyOpacity"] == "0"
    assert harness["backTo"] == "0"
    assert harness["config"] == [100, None]


def test_the_manifest_carries_the_settings(app, paired_display):
    with app.app_context():
        d = db.session.get(Display, paired_display[0])
        assert options.settings_payload(d)["brightness"] == 100 and options.settings_payload(d)["dim"] is None
        d.brightness, d.dim_level, d.dim_from, d.dim_to = 70, 25, "22:00", "06:30"
        Setting.set("timezone", "Europe/Paris")
        p = options.settings_payload(d)
        assert p["brightness"] == 70 and p["dim"] == {"level": 25, "from": "22:00", "to": "06:30", "tz": "Europe/Paris"}
        d.brightness = 3                                                           # out of range in the database: clamped
        assert options.settings_payload(d)["brightness"] == 10
        d.dim_to = "22:00"
        assert options.settings_payload(d)["dim"] is None                          # equal times: no window
        d.dim_to = "bad"
        assert options.settings_payload(d)["dim"] is None


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _form(did, **over):
    base = {"name": "Lobby", "playlist": "0", "group": "0", "orientation": "0", "fit_override": "", "volume": "100", "brightness": "100",
            "dim_level": "40", "dim_from": "", "dim_to": "", "show_offline_banner": "y", "resume_after_min": "5"}
    base.update(over)
    return base


def test_the_edit_page_saves_brightness_and_the_night_window(app, admin, paired_display):
    did = paired_display[0]
    page = admin.get(f"/displays/{did}/edit").get_data(as_text=True)
    assert "Brightness (10-100 %)" in page and "Night brightness" in page
    r = admin.post(f"/displays/{did}/edit", data=_form(did, brightness="80", dim_level="30", dim_from="22:00", dim_to="06:00"), follow_redirects=True)
    assert r.status_code == 200 and b"Display saved" in r.data
    with app.app_context():
        d = db.session.get(Display, did)
        assert (d.brightness, d.dim_level, d.dim_from, d.dim_to) == (80, 30, "22:00", "06:00")
    assert b"dimming from 22:00 to 06:00" in admin.get(f"/displays/{did}/edit").data


@pytest.mark.parametrize("over,msg", [({"brightness": "5"}, None), ({"brightness": "101"}, None), ({"dim_from": "22:00"}, b"both times"),
                                      ({"dim_from": "25:00", "dim_to": "06:00"}, b"HH:MM"), ({"dim_from": "22:00", "dim_to": "22:00"}, b"must differ")])
def test_bad_brightness_values_are_refused(app, admin, paired_display, over, msg):
    did = paired_display[0]
    r = admin.post(f"/displays/{did}/edit", data=_form(did, **over))
    assert r.status_code == 200 and b"Display saved" not in r.data
    if msg:
        assert msg in r.data
    with app.app_context():
        assert db.session.get(Display, did).brightness == 100


def test_clearing_the_times_turns_the_night_window_off(app, admin, paired_display):
    did = paired_display[0]
    admin.post(f"/displays/{did}/edit", data=_form(did, dim_from="22:00", dim_to="06:00"))
    admin.post(f"/displays/{did}/edit", data=_form(did, dim_from="", dim_to=""))
    with app.app_context():
        d = db.session.get(Display, did)
        assert d.dim_from is None and d.dim_to is None and options.settings_payload(d)["dim"] is None


def test_bulk_edit_can_set_the_brightness_of_several_screens(app, admin, paired_display):
    did = paired_display[0]
    page = admin.get(f"/displays/bulk?id={did}")
    assert b"Brightness (10-100 %)" in page.data
    tok = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', page.get_data(as_text=True)).group(1)
    r = admin.post("/displays/bulk", data={"csrf_token": tok, "id": [did], "apply": "1", "playlist": "", "group": "", "orientation": "", "fit_override": "",
                                           "volume": "", "brightness": "65", "show_offline_banner": ""}, follow_redirects=True)
    assert b"1 of 1 screen changed" in r.data
    with app.app_context():
        assert db.session.get(Display, did).brightness == 65
