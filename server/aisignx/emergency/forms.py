from flask_wtf import FlaskForm
from sqlalchemy.orm import selectinload
from wtforms import BooleanField, SelectField, StringField, TextAreaField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from .. import targets
from ..models import Playlist, PlaylistItem
from ..models.emergency import LEVELS
from ..schedules.resolve import is_playable_playlist

NO_PLAYLIST = SelectChoice(0, "No playlist, show text only")
LEVEL_CHOICES = [SelectChoice(level, level.capitalize()) for level in LEVELS]


def playlist_choices() -> list[SelectChoice]:
    return [NO_PLAYLIST] + [SelectChoice(p.id, p.name)
                            for p in Playlist.query.order_by(Playlist.name)]


class EmergencyForm(FlaskForm):
    title = StringField("Title", validators=[DataRequired(), Length(max=120)],
                        render_kw={"placeholder": "e.g. Fire drill", "autocomplete": "off"})
    message = TextAreaField("Message", validators=[Optional(), Length(max=1000)],
                            render_kw={"rows": 3, "placeholder": "Leave by the nearest exit."})
    level = SelectField("Level", choices=LEVEL_CHOICES, default="critical")
    # No default: a request without a target is rejected rather than sent to all screens.
    target = SelectField("Show on")
    playlist = SelectField("Playlist", coerce=int, default=0)
    show_text = BooleanField("Show the title and message", default=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.target.choices = targets.choices(placeholder=True)
        self.playlist.choices = playlist_choices()

    def validate_target(self, field) -> None:
        targets.parse(field.data)  # raises ValidationError for a bad or missing target

    def validate_playlist(self, field) -> None:
        if not field.data:
            return
        playlist = (Playlist.query.filter(Playlist.id == field.data)
                    .options(selectinload(Playlist.items).selectinload(PlaylistItem.media))
                    .first())
        if playlist is None:
            raise ValidationError("That playlist no longer exists.")
        if not is_playable_playlist(playlist):
            raise ValidationError("This playlist has nothing the screens can play.")

    def validate_show_text(self, field) -> None:
        if not field.data and not self.playlist.data:
            raise ValidationError("Without a playlist the text must be shown.")


class ActionForm(FlaskForm):
    """Empty form used only for its CSRF token."""
