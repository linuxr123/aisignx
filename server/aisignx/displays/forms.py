from flask_wtf import FlaskForm
from wtforms import (BooleanField, IntegerField, PasswordField, SelectField, StringField,
                     TextAreaField)
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, InputRequired, Length, NumberRange, Optional, Regexp

from ..models import Display, DisplayGroup, Playlist

NO_PLAYLIST = SelectChoice(0, "No playlist")
NEW_DISPLAY = SelectChoice(0, "New display")
NO_GROUP = SelectChoice(0, "No group")
DEFAULT_PLAYLIST_LABEL = "Default playlist"
DEFAULT_PLAYLIST_HELP = "Plays when no schedule matches."
ORIENTATION_CHOICES = [
    SelectChoice(0, "Landscape (normal)"),
    SelectChoice(90, "Portrait (rotated 90°)"),
    SelectChoice(180, "Landscape upside down (180°)"),
    SelectChoice(270, "Portrait (rotated 270°)"),
]
FIT_CHOICES = [
    SelectChoice("", "Per item (default)"),
    SelectChoice("fit", "Fit (letterbox)"),
    SelectChoice("fill", "Fill (crop)"),
    SelectChoice("stretch", "Stretch"),
]


def playlist_choices() -> list[SelectChoice]:
    return [NO_PLAYLIST] + [SelectChoice(p.id, p.name)
                            for p in Playlist.query.order_by(Playlist.name)]


def group_choices() -> list[SelectChoice]:
    return [NO_GROUP] + [SelectChoice(g.id, g.name)
                         for g in DisplayGroup.query.order_by(DisplayGroup.name)]


def display_choices() -> list[SelectChoice]:
    return [NEW_DISPLAY] + [SelectChoice(d.id, d.name)
                            for d in Display.query.order_by(Display.name)]


class PairForm(FlaskForm):
    code = StringField("Code shown on the screen", validators=[DataRequired(), Length(max=20)],
                       render_kw={"placeholder": "K7P-Q4X", "autocomplete": "off",
                                  "autocapitalize": "characters", "spellcheck": "false"})
    pair_as = SelectField("Pair as", coerce=int, default=0)
    name = StringField("Name", validators=[Optional(), Length(max=100)],
                       render_kw={"placeholder": "e.g. Lobby"})
    playlist = SelectField(DEFAULT_PLAYLIST_LABEL, coerce=int, default=0,
                           description=DEFAULT_PLAYLIST_HELP)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pair_as.choices = display_choices()
        self.playlist.choices = playlist_choices()

    def validate(self, extra_validators=None) -> bool:
        ok = super().validate(extra_validators)
        if not self.pair_as.data and not (self.name.data or "").strip():
            self.name.errors = [*self.name.errors, "Give the new display a name."]
            ok = False
        return ok


class DisplayForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)])
    playlist = SelectField(DEFAULT_PLAYLIST_LABEL, coerce=int, default=0,
                           description=DEFAULT_PLAYLIST_HELP)
    group = SelectField("Group", coerce=int, default=0)
    location = StringField("Location", validators=[Optional(), Length(max=100)],
                           render_kw={"placeholder": "e.g. Ground floor, reception"})
    description = TextAreaField("Notes", validators=[Optional(), Length(max=2000)],
                                render_kw={"rows": 3})

    # Screen
    orientation = SelectField("Orientation", coerce=int, default=0,
                              choices=ORIENTATION_CHOICES)
    fit_override = SelectField("Fit", default="", choices=FIT_CHOICES)
    volume = IntegerField("Volume (0-100)", default=100,
                          validators=[InputRequired(), NumberRange(0, 100)],
                          render_kw={"min": 0, "max": 100})
    show_offline_banner = BooleanField("Show a small banner when the screen is offline",
                                       default=True)

    # Kiosk lock: admins only. Removed from the form for everyone else, so an editor
    # posting these fields changes nothing.
    allow_input = BooleanField("Allow touch, mouse and keyboard")
    unlock_pin = PasswordField(
        "New unlock PIN",
        validators=[Optional(), Regexp(r"^\d{4,8}$", message="4 to 8 digits")],
        render_kw={"autocomplete": "new-password", "inputmode": "numeric"})
    clear_pin = BooleanField("Remove the unlock PIN")

    KIOSK_FIELDS = ("allow_input", "unlock_pin", "clear_pin")

    def __init__(self, *args, admin: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.playlist.choices = playlist_choices()
        self.group.choices = group_choices()
        self.is_admin_form = admin
        if not admin:
            for name in self.KIOSK_FIELDS:
                delattr(self, name)

    def validate(self, extra_validators=None) -> bool:
        ok = super().validate(extra_validators)
        if self.is_admin_form and self.clear_pin.data and self.unlock_pin.data:
            self.unlock_pin.errors = [*self.unlock_pin.errors,
                                      "Either remove the PIN or set a new one."]
            ok = False
        return ok


class ActionForm(FlaskForm):
    """Empty form used only for its CSRF token."""
