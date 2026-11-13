"""Per-display options: model defaults, migration, PIN hashing, edit form and manifest."""
import json
import re
from datetime import timedelta

import pytest
from flask_migrate import downgrade, upgrade
from sqlalchemy import inspect

from aisignx.displays import options
from aisignx.extensions import db
from aisignx.models import Display, PairingCode
from aisignx.timeutil import utcnow

from .conftest import DISPLAY_TOKEN, bearer, login, make_user
from .test_player_api import API, content  # noqa: F401  (fixture)
from .test_player_stream import _drain, listener  # noqa: F401  (fixture)

SALT = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
VECTORS = [
    ("2468", 200_000, "37cff31b6ad22b037e276408739e6702ab1a83eca9924fef485e1089b3c3ce2d"),
    ("12345678", 1000, "42b99a197a7d9cafa9fa5b60091da410edb6f3c1cc4e159a5f901b5348abb716"),
]
DEFAULTS = {"orientation": 0, "volume": 100, "show_offline_banner": True,
            "allow_input": False, "unlock": None}


def _form(**overrides) -> dict:
    data = {"name": "Lobby", "playlist": 0, "group": 0, "orientation": 0, "fit_override": "",
            "volume": 100, "show_offline_banner": "y"}
    data.update(overrides)
    return {k: v for k, v in data.items() if v is not None}


def _display(app, display_id) -> Display:
    with app.app_context():
        display = db.session.get(Display, display_id)
        db.session.expunge(display)
        return display


# --- PIN hashing ------------------------------------------------------------------------

@pytest.mark.parametrize("pin,iterations,expected", VECTORS)
def test_pin_vectors(pin, iterations, expected):
    stored = options.hash_pin(pin, salt=SALT, iterations=iterations)
    assert stored == f"pbkdf2-sha256${iterations}${SALT.hex()}${expected}"
    assert options.verify_pin(stored, pin)
    assert not options.verify_pin(stored, pin + "0")


def test_hash_pin_uses_random_salt_and_default_iterations():
    a, b = options.hash_pin("2468"), options.hash_pin("2468")
    assert a != b
    assert re.fullmatch(r"pbkdf2-sha256\$200000\$[0-9a-f]{32}\$[0-9a-f]{64}", a)
    assert options.verify_pin(a, "2468")


@pytest.mark.parametrize("stored", [
    None, "", "2468",
    "pbkdf2-sha256$0$" + "0" * 32 + "$" + "0" * 64,
    "md5$1000$" + "0" * 32 + "$" + "0" * 64,
    "pbkdf2-sha256$1000$xyz$" + "0" * 64,
])
def test_malformed_hash_gives_no_unlock(stored):
    assert options.unlock_payload(Display(unlock_pin_hash=stored)) is None
    assert not options.verify_pin(stored, "2468")


def test_settings_payload_defaults_and_clamps():
    assert options.settings_payload(Display(orientation=0, volume=100,
                                            show_offline_banner=True,
                                            allow_input=False)) == DEFAULTS
    odd = options.settings_payload(Display(orientation=45, volume=250,
                                           show_offline_banner=False, allow_input=True))
    assert (odd["orientation"], odd["volume"]) == (0, 100)
    assert options.settings_payload(Display(orientation=270, volume=-5))["volume"] == 0
    stored = options.hash_pin("2468", salt=SALT, iterations=1000)
    unlock = options.settings_payload(Display(unlock_pin_hash=stored))["unlock"]
    assert unlock == {"alg": "pbkdf2-sha256", "iter": 1000, "salt": SALT.hex(),
                      "hash": stored.rsplit("$", 1)[1]}


def test_parse_diag():
    assert options.parse_diag(None) is None
    assert options.parse_diag("{not json") is None
    assert options.parse_diag("[1, 2]") is None
    assert options.parse_diag('{"ua": "x"}') == {"ua": "x"}


# --- model and migration ----------------------------------------------------------------

def test_new_display_defaults(app, configured, paired_display):
    display = _display(app, paired_display[0])
    assert (display.orientation, display.volume, display.show_offline_banner,
            display.allow_input) == (0, 100, True, False)
    assert display.fit_override is None and display.unlock_pin_hash is None


def test_migration_keeps_pairing_codes_and_sets_defaults(app):
    with app.app_context():
        downgrade(revision="b7d3e1a95c20")
        assert "orientation" not in {c["name"] for c in inspect(db.engine).get_columns("displays")}
        now = utcnow().replace(tzinfo=None)
        with db.engine.begin() as conn:
            conn.execute(db.text("INSERT INTO displays (name, created_at) VALUES ('Old', :now)"),
                         {"now": now.isoformat(sep=" ")})
            display_id = conn.execute(db.text("SELECT id FROM displays")).scalar()
            conn.execute(db.text(
                "INSERT INTO pairing_codes (code, secret_hash, created_at, expires_at, display_id)"
                " VALUES ('K7PQ4X', :h, :now, :exp, :d)"),
                {"h": "a" * 64, "now": now.isoformat(sep=" "),
                 "exp": (now + timedelta(minutes=2)).isoformat(sep=" "), "d": display_id})
        upgrade()
        db.session.remove()
        assert PairingCode.query.count() == 1
        display = db.session.get(Display, display_id)
        assert (display.orientation, display.volume, display.show_offline_banner,
                display.allow_input) == (0, 100, True, False)
        downgrade(revision="b7d3e1a95c20")
        upgrade()
        assert PairingCode.query.count() == 1


# --- manifest ---------------------------------------------------------------------------

def test_manifest_settings_default(app, client, content):  # noqa: F811
    body = client.get(f"{API}/manifest", headers=bearer(content["token"])).get_json()
    assert body["settings"] == DEFAULTS


def test_fit_override_replaces_every_item(app, client, content):  # noqa: F811
    with app.app_context():
        db.session.get(Display, content["display_id"]).fit_override = "stretch"
        db.session.commit()
    body = client.get(f"{API}/manifest", headers=bearer(content["token"])).get_json()
    assert body["items"] and {i["fit"] for i in body["items"]} == {"stretch"}


def test_manifest_unlock_and_no_admin_notes(app, client, content):  # noqa: F811
    with app.app_context():
        display = db.session.get(Display, content["display_id"])
        display.location = "Secret basement"
        display.description = "Behind the vending machine"
        options.set_unlock_pin(display, "97531")
        db.session.commit()
    resp = client.get(f"{API}/manifest", headers=bearer(content["token"]))
    unlock = resp.get_json()["settings"]["unlock"]
    assert unlock["alg"] == "pbkdf2-sha256" and unlock["iter"] == options.PBKDF2_ITER
    assert re.fullmatch(r"[0-9a-f]{32}", unlock["salt"])
    assert re.fullmatch(r"[0-9a-f]{64}", unlock["hash"])
    raw = resp.get_data(as_text=True)
    assert "97531" not in raw
    assert "Secret basement" not in raw and "vending" not in raw


# --- edit form --------------------------------------------------------------------------

def test_admin_sets_options_and_pin(app, admin_client, paired_display, listener):  # noqa: F811
    display_id = paired_display[0]
    _drain(listener)
    resp = admin_client.post(f"/displays/{display_id}/edit", data=_form(
        location="  Reception ", description="", orientation=90, fit_override="fill",
        volume=35, show_offline_banner=None, allow_input="y", unlock_pin="2468"))
    assert resp.status_code == 302
    assert _drain(listener) == [("manifest", {})]
    display = _display(app, display_id)
    assert display.location == "Reception" and display.description is None
    assert (display.orientation, display.fit_override, display.volume) == (90, "fill", 35)
    assert display.show_offline_banner is False and display.allow_input is True
    assert options.verify_pin(display.unlock_pin_hash, "2468")
    stored = display.unlock_pin_hash

    # Blank keeps the PIN; the edit page never shows it.
    page = admin_client.get(f"/displays/{display_id}/edit").get_data(as_text=True)
    assert "2468" not in page and stored.split("$")[3] not in page
    assert 'value="Reception"' in page
    admin_client.post(f"/displays/{display_id}/edit", data=_form(allow_input="y",
                                                                  unlock_pin=""))
    assert _display(app, display_id).unlock_pin_hash == stored

    # Clearing it together with a new PIN is an error; the error page shows no PIN.
    resp = admin_client.post(f"/displays/{display_id}/edit",
                             data=_form(clear_pin="y", unlock_pin="1357"))
    assert resp.status_code == 200 and "1357" not in resp.get_data(as_text=True)
    assert _display(app, display_id).unlock_pin_hash == stored

    admin_client.post(f"/displays/{display_id}/edit", data=_form(clear_pin="y"))
    display = _display(app, display_id)
    assert display.unlock_pin_hash is None and display.allow_input is False


@pytest.mark.parametrize("field,value", [
    ("unlock_pin", "12a4"), ("unlock_pin", "123"), ("unlock_pin", "123456789"),
    ("orientation", 45), ("volume", 101), ("volume", -1), ("volume", ""),
    ("fit_override", "zoom"),
])
def test_edit_rejects_bad_values(app, admin_client, paired_display, field, value):
    display_id = paired_display[0]
    resp = admin_client.post(f"/displays/{display_id}/edit",
                             data=_form(name="Changed", **{field: value}))
    assert resp.status_code == 200
    assert "is-invalid" in resp.get_data(as_text=True)
    display = _display(app, display_id)
    assert display.name == "Lobby" and display.unlock_pin_hash is None


def test_editor_cannot_touch_kiosk_lock(app, configured, client, paired_display):
    display_id = paired_display[0]
    with app.app_context():
        display = db.session.get(Display, display_id)
        options.set_unlock_pin(display, "2468")
        db.session.commit()
        stored = display.unlock_pin_hash
    make_user(app, "editor", "editor")
    login(client, "editor")
    page = client.get(f"/displays/{display_id}/edit").get_data(as_text=True)
    assert 'name="unlock_pin"' not in page and 'name="allow_input"' not in page
    assert "Unlock PIN: set" in page and "admins only" in page

    resp = client.post(f"/displays/{display_id}/edit",
                       data=_form(volume=50, allow_input="y", unlock_pin="1111",
                                  clear_pin="y"))
    assert resp.status_code == 302
    display = _display(app, display_id)
    assert display.volume == 50
    assert display.allow_input is False and display.unlock_pin_hash == stored


def test_detail_shows_settings_card(app, admin_client, paired_display):
    display_id = paired_display[0]
    with app.app_context():
        display = db.session.get(Display, display_id)
        display.location = "Reception <b>desk</b>"
        options.set_unlock_pin(display, "8642")
        db.session.commit()
    page = admin_client.get(f"/displays/{display_id}").get_data(as_text=True)
    assert "Reception &lt;b&gt;desk&lt;/b&gt;" in page
    assert "Unlock PIN" in page and "8642" not in page


def test_manifest_has_no_digit_only_strings(app, client, content):  # noqa: F811
    with app.app_context():
        options.set_unlock_pin(db.session.get(Display, content["display_id"]), "4321")
        db.session.commit()
    body = client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).get_json()

    def strings(value):
        if isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
        elif isinstance(value, str):
            yield value

    assert not [s for s in strings(body) if re.fullmatch(r"\d{4,8}", s)]
    assert "4321" not in json.dumps(body["settings"])
