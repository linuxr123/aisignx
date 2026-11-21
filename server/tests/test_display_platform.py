"""The display settings speak the language of the system the screen runs."""
import json

import pytest

from aisignx.displays import platform
from aisignx.extensions import db
from aisignx.models import Display

from .conftest import login, make_user

WIN = {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Electron/30", "shell": {"name": "electron", "version": "3.5.0"}}
AND = {"ua": "Mozilla/5.0 (Linux; Android 13; SM-T500) AppleWebKit/537.36 Chrome/120", "shell": {"name": "android", "version": "3.0.6"}}
LIN = {"ua": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Electron/30", "shell": {"name": "electron", "version": "3.5.0"}}
BROWSER = {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124 Safari/537.36"}
LINUX_BROWSER = {"ua": "Mozilla/5.0 (X11; Linux x86_64) Firefox/125.0"}


@pytest.mark.parametrize("diag,key", [(WIN, "windows"), (AND, "android"), (LIN, "linux"), (BROWSER, "browser"),
                                      (LINUX_BROWSER, "browser"), ({}, "unknown"), (None, "unknown"), ({"ua": ""}, "unknown")])
def test_the_system_is_worked_out_from_what_the_player_reported(diag, key):
    assert platform.of_diag(diag) == key


def test_every_system_has_every_word():
    keys = set(platform.WORDS["unknown"])
    for name, words in platform.WORDS.items():
        assert set(words) == keys, name
    assert platform.words("nonsense") is platform.WORDS["unknown"]


@pytest.fixture
def admin(app, configured):
    make_user(app, "boss", "admin")
    c = app.test_client()
    login(c, "boss")
    return c


def _display(app, paired_display, diag):
    did = paired_display[0]
    with app.app_context():
        db.session.get(Display, did).diagnostics = json.dumps(diag)
        db.session.commit()
    return did


@pytest.mark.parametrize("diag,title,needle", [
    (WIN, "Windows settings", "Restart the PC"),
    (AND, "Android settings", "Reboot the device"),
    (LIN, "Linux settings", "Reboot the machine"),
    (BROWSER, "Browser settings", "apps only"),
    ({}, "Device settings", "Reboot the device"),
])
def test_the_edit_page_uses_the_words_of_the_screens_system(app, admin, paired_display, diag, title, needle):
    did = _display(app, paired_display, diag)
    page = admin.get(f"/displays/{did}/edit").get_data(as_text=True)
    assert title in page and needle in page
    other = {"Windows settings", "Android settings", "Linux settings", "Browser settings"} - {title}
    assert not any(o in page for o in other)


def test_an_editor_still_sees_the_read_only_summary_with_the_systems_title(app, configured, paired_display):
    did = _display(app, paired_display, AND)
    make_user(app, "ed", "editor")
    c = app.test_client()
    login(c, "ed")
    page = c.get(f"/displays/{did}/edit").get_data(as_text=True)
    assert "Android settings" in page and "admins only" in page
