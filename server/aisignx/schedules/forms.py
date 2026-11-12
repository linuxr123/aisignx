from datetime import time

from flask_wtf import FlaskForm
from wtforms import (BooleanField, DateField, IntegerField, SelectField, SelectMultipleField,
                     StringField, TimeField)
from wtforms.fields.choices import SelectChoice
from wtforms.validators import (DataRequired, InputRequired, Length, NumberRange, Optional,
                                ValidationError)
from wtforms.widgets import CheckboxInput, ListWidget

from .. import targets
from ..extensions import db
from ..models import Playlist
from ..models.schedule import ALL_DAYS, DAY_NAMES, PRIORITY_MAX

DAY_CHOICES = [SelectChoice(i, name) for i, name in enumerate(DAY_NAMES)]
TIME_FORMAT = "%H:%M"


class MultiCheckboxField(SelectMultipleField):
    """A select-multiple rendered as checkboxes."""
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


def playlist_choices() -> list[SelectChoice]:
    return [SelectChoice(p.id, p.name) for p in Playlist.query.order_by(Playlist.name)]


def days_to_mask(days) -> int:
    return sum(1 << int(d) for d in set(days or ()) if 0 <= int(d) <= 6)


def mask_to_days(mask: int) -> list[int]:
    return [i for i in range(7) if (mask or 0) >> i & 1]


class ScheduleForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)],
                       render_kw={"placeholder": "e.g. Lunch menu"})
    playlist = SelectField("Playlist", coerce=int, validate_choice=False)
    target = SelectField("Show on", default=targets.ALL, validate_choice=False)
    days = MultiCheckboxField("Days", coerce=int, choices=DAY_CHOICES,
                              default=mask_to_days(ALL_DAYS))
    all_day = BooleanField("All day", default=False)
    start_time = TimeField("From", format=TIME_FORMAT, validators=[Optional()],
                           default=time(9, 0))
    end_time = TimeField("Until", format=TIME_FORMAT, validators=[Optional()],
                         default=time(17, 0))
    start_date = DateField("First day", validators=[Optional()])
    end_date = DateField("Last day", validators=[Optional()])
    priority = IntegerField("Priority", default=0,
                            validators=[InputRequired(), NumberRange(min=0, max=PRIORITY_MAX)])
    enabled = BooleanField("Enabled", default=True)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.playlist.choices = playlist_choices()
        self.target.choices = targets.choices()
        self.parsed_target = None

    def validate_playlist(self, field):
        if not field.data or db.session.get(Playlist, field.data) is None:
            raise ValidationError("Choose a playlist.")

    def validate_target(self, field):
        self.parsed_target = targets.parse(field.data)

    def validate_days(self, field):
        if not days_to_mask(field.data):
            raise ValidationError("Pick at least one day.")

    def validate(self, extra_validators=None) -> bool:
        ok = super().validate(extra_validators)
        if not self.all_day.data:
            start, end = self.start_time.data, self.end_time.data
            for f in (self.start_time, self.end_time):
                if f.data is None and not f.errors:
                    f.errors = [*f.errors, "Enter a time, or tick All day."]
                    ok = False
            if start is not None and end is not None and start == end:
                self.end_time.errors = [*self.end_time.errors,
                                        "Must differ from the start time."]
                ok = False
        if (self.start_date.data and self.end_date.data
                and self.end_date.data < self.start_date.data):
            self.end_date.errors = [*self.end_date.errors,
                                    "Must be on or after the first day."]
            ok = False
        return ok

    @property
    def is_overnight(self) -> bool:
        start, end = self.start_time.data, self.end_time.data
        return not self.all_day.data and start is not None and end is not None and end < start

    def load(self, s) -> None:
        """Fill every field from a schedule (GET of the edit page)."""
        self.name.data = s.name
        self.playlist.data = s.playlist_id
        self.target.data = targets.encode(s)
        self.days.data = mask_to_days(s.days_mask)
        self.all_day.data = s.start_time is None
        self.start_time.data = s.start_time
        self.end_time.data = s.end_time
        self.start_date.data = s.start_date
        self.end_date.data = s.end_date
        self.priority.data = s.priority
        self.enabled.data = s.enabled

    def apply(self, s) -> None:
        """Copy every field onto a schedule, so clearing a field really clears it."""
        scope, display_id, group_id = self.parsed_target or targets.parse(self.target.data)
        s.name = self.name.data.strip()
        s.playlist_id = self.playlist.data
        s.scope, s.display_id, s.group_id = scope, display_id, group_id
        s.days_mask = days_to_mask(self.days.data)
        if self.all_day.data:
            s.start_time = s.end_time = None
        else:
            s.start_time = self.start_time.data.replace(second=0, microsecond=0)
            s.end_time = self.end_time.data.replace(second=0, microsecond=0)
        s.start_date = self.start_date.data
        s.end_date = self.end_date.data
        s.priority = self.priority.data
        s.enabled = bool(self.enabled.data)
