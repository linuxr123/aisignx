from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from ..media.forms import MAX_DURATION
from ..models.playlist import DEFAULT_TRANSITIONS, FIT_MODES, TRANSITIONS


def _label(name: str) -> str:
    return name.replace("-", " ").capitalize()


TRANSITION_CHOICES = [SelectChoice(t, _label(t)) for t in TRANSITIONS]
DEFAULT_TRANSITION_CHOICES = [SelectChoice(t, _label(t)) for t in DEFAULT_TRANSITIONS]
ITEM_TRANSITION_CHOICES = [SelectChoice("", "Playlist default")] + TRANSITION_CHOICES
_FIT_LABELS = {"fit": "Fit (letterbox)", "fill": "Fill (crop)", "stretch": "Stretch"}
FIT_CHOICES = [SelectChoice(mode, _FIT_LABELS[mode]) for mode in FIT_MODES]


class PlaylistForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=120)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=2000)])
    default_transition = SelectField("Default transition", choices=DEFAULT_TRANSITION_CHOICES,
                                     default="fade")


class ItemForm(FlaskForm):
    duration = IntegerField("Duration (s)", validators=[Optional(), NumberRange(min=1, max=MAX_DURATION)])
    transition = SelectField("Transition", choices=ITEM_TRANSITION_CHOICES, validate_choice=True)
    fit_mode = SelectField("Fit", choices=FIT_CHOICES)
    mute = BooleanField("Mute")


class ActionForm(FlaskForm):
    """Empty form used only for its CSRF token."""
