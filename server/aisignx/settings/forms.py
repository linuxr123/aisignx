from functools import lru_cache
from zoneinfo import available_timezones

from flask_wtf import FlaskForm
from wtforms import IntegerField, SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, NumberRange


@lru_cache(maxsize=1)
def timezone_choices() -> list[SelectChoice]:
    zones = sorted(z for z in available_timezones() if "/" in z or z == "UTC")
    return [SelectChoice(z, z.replace("_", " ")) for z in zones]


class SettingsForm(FlaskForm):
    org_name = StringField("Organization name", validators=[DataRequired(), Length(max=120)])
    timezone = SelectField("Time zone", validators=[DataRequired()])
    default_duration = IntegerField(
        "Default duration for new images and web pages (seconds)",
        validators=[DataRequired(), NumberRange(min=1, max=24 * 3600)],
    )
