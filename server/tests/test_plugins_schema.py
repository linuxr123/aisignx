"""Plugin config schema: field definitions and server-side validation of every type."""
import pytest

from aisignx.extensions import db
from aisignx.models import Media
from aisignx.plugins import schema
from aisignx.plugins.schema import SchemaError, check_fields, validate

from .plugin_fixtures import papp  # noqa: F401


def one(ftype, **kw):
    return check_fields([{"name": "f", "type": ftype, **kw}])


def value(fields, raw, check_refs=False):
    config, errors = validate(fields, {"f": raw}, check_refs=check_refs)
    return config.get("f"), errors.get("f")


def test_string_trimmed_capped_and_pattern():
    fields = one("string", max_length=5, pattern="[A-Z]+")
    assert value(fields, "  ABC ") == ("ABC", None)
    assert value(fields, "ABCDEF")[1]
    assert value(fields, "abc")[1]
    assert value(fields, "A\x00B")[1]
    assert value(one("string"), "x" * 200)[0] == "x" * 200
    assert value(one("string"), "x" * 201)[1]
    assert value(one("string", required=True), "   ")[1] == "This field is required."


def test_text_keeps_line_breaks():
    fields = one("text")
    assert value(fields, "a\r\nb\rc") == ("a\nb\nc", None)
    assert value(fields, "x" * 5001)[1]


def test_integer_bounds_and_default():
    fields = one("integer", min=1, max=10, default=4)
    assert value(fields, "7") == (7, None)
    assert value(fields, "") == (4, None)
    assert value(fields, None) == (4, None)
    assert value(fields, "11")[1] and value(fields, "0")[1] and value(fields, "2.5")[1]
    assert value(fields, True)[1]
    with pytest.raises(SchemaError):
        one("integer")  # min/max required
    with pytest.raises(SchemaError):
        one("integer", min=5, max=1)


def test_boolean():
    fields = one("boolean")
    assert value(fields, None) == (False, None)
    assert value(fields, "1") == (True, None)
    assert value(fields, "on") == (True, None)
    assert value(fields, False) == (False, None)
    assert value(fields, "maybe")[1]


def test_select():
    fields = one("select", options=[{"value": "a", "label": "A"}, "b"])
    assert value(fields, "b") == ("b", None)
    assert value(fields, "") == ("a", None)  # first option is the default
    assert value(fields, "c")[1]
    with pytest.raises(SchemaError):
        one("select", options=[])


@pytest.mark.parametrize("url,ok", [
    ("https://example.com/feed", True), ("http://example.com", True),
    ("ftp://example.com", False), ("javascript:alert(1)", False),
    ("https://user:pw@example.com", False), ("https://exa mple.com", False),
    ("https://example.com:99999", False), ("https://" + "a" * 2050 + ".com", False),
])
def test_url(url, ok):
    assert (value(one("url"), url)[1] is None) is ok


def test_timezone():
    fields = one("timezone")
    assert value(fields, "") == ("", None)
    assert value(fields, "Europe/Amsterdam") == ("Europe/Amsterdam", None)
    assert value(fields, "Mars/Base")[1]


def test_location_rounding_label_and_latlon_text():
    fields = one("location")
    loc, err = value(fields, {"q": "Amsterdam", "lat": "52.3675734", "lon": "4.9041389",
                              "label": ""})
    assert err is None and loc == {"q": "Amsterdam", "lat": 52.3676, "lon": 4.9041,
                                   "label": "Amsterdam"}
    loc, err = value(fields, {"q": "52.1, 5.2", "label": "Office"})
    assert err is None and (loc["lat"], loc["lon"], loc["label"]) == (52.1, 5.2, "Office")
    assert value(fields, {"q": "Somewhere"})[1]  # a name without Find
    assert value(fields, {"q": "x", "lat": "91", "lon": "0"})[1]
    assert value(fields, {"q": "x", "lat": "0", "lon": "-181"})[1]
    assert value(fields, {"q": "x", "lat": "nan", "lon": "0"})[1]
    assert value(fields, None) == (None, None)
    assert value(fields, {"q": "", "lat": "", "lon": "", "label": "x"}) == (None, None)
    assert value(one("location", required=True), None)[1]


def test_media_multi_checks_image_ids(papp):
    fields = one("media_multi", max=3)
    with papp.app_context():
        img = Media(name="i", media_type="image", stored_name="a.png")
        vid = Media(name="v", media_type="video", stored_name="b.mp4")
        db.session.add_all([img, vid])
        db.session.commit()
        assert value(fields, [str(img.id), img.id], check_refs=True) == ([img.id], None)
        assert value(fields, [vid.id], check_refs=True)[1]
        assert value(fields, [999], check_refs=True)[1]
        assert value(fields, [999], check_refs=False) == ([999], None)
        assert value(fields, [1, 2, 3, 4])[1]
        assert value(fields, ["x"])[1]
        assert value(fields, []) == ([], None)


def test_unknown_keys_dropped_and_size_cap():
    fields = check_fields([{"name": "a", "type": "text", "max_length": 20000}])
    config, errors = validate(fields, {"a": "x", "evil": "y"})
    assert config == {"a": "x"} and not errors
    config, errors = validate(fields, {"a": "x" * 17000})
    assert errors == {"_": "These settings are too large (16 KB at most)."}


def test_private_fields_stripped():
    fields = check_fields([{"name": "url", "type": "url", "private": True},
                           {"name": "title", "type": "string"}])
    config, _ = validate(fields, {"url": "https://example.com", "title": "T"})
    assert schema.public_config(fields, config) == {"title": "T"}


def test_show_if_is_ignored_by_the_server():
    fields = check_fields([{"name": "on", "type": "boolean"},
                           {"name": "x", "type": "integer", "min": 1, "max": 2,
                            "show_if": {"on": True}}])
    _config, errors = validate(fields, {"on": False, "x": "9"})
    assert "x" in errors
    with pytest.raises(SchemaError, match="unknown field"):
        check_fields([{"name": "x", "type": "string", "show_if": {"nope": 1}}])


def test_bad_definitions():
    for bad in ([{"name": "Bad", "type": "string"}], [{"name": "a", "type": "color"}],
                [{"name": "a", "type": "string"}, {"name": "a", "type": "text"}],
                [{"name": "a", "type": "integer", "min": 1, "max": 3, "default": 9}],
                [{"name": "a", "type": "text", "pattern": "x"}], "not a list"):
        with pytest.raises(SchemaError):
            check_fields(bad)


def test_defaults_and_settings_round_trip():
    fields = check_fields([{"name": "n", "type": "integer", "min": 1, "max": 9, "default": 2},
                           {"name": "b", "type": "boolean", "default": True},
                           {"name": "s", "type": "select", "options": ["x", "y"]},
                           {"name": "loc", "type": "location"}])
    assert schema.defaults(fields) == {"n": 2, "b": True, "s": "x", "loc": None}
    loc_field = fields[3]
    stored = schema.to_setting(loc_field, {"q": "a", "lat": 1.0, "lon": 2.0, "label": "a"})
    assert schema.validate_value(loc_field, schema.from_setting(loc_field, stored))[0]["lat"] == 1.0
    assert schema.to_setting(fields[1], False) == "0"
