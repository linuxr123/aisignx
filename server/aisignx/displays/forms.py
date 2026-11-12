from flask_wtf import FlaskForm
from wtforms import SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, Optional

from ..models import Display, DisplayGroup, Playlist

NO_PLAYLIST = SelectChoice(0, "No playlist")
NEW_DISPLAY = SelectChoice(0, "New display")
NO_GROUP = SelectChoice(0, "No group")
DEFAULT_PLAYLIST_LABEL = "Default playlist"
DEFAULT_PLAYLIST_HELP = "Plays when no schedule matches."


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.playlist.choices = playlist_choices()
        self.group.choices = group_choices()


class ActionForm(FlaskForm):
    """Empty form used only for its CSRF token."""
