from functools import lru_cache
from zoneinfo import available_timezones

from flask_wtf import FlaskForm
from wtforms import SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length


@lru_cache(maxsize=1)
def timezone_choices() -> list[SelectChoice]:
    zones = sorted(z for z in available_timezones() if "/" in z or z == "UTC")
    return [SelectChoice(z, z.replace("_", " ")) for z in zones]


class SettingsForm(FlaskForm):
    org_name = StringField("Organization name", validators=[DataRequired(), Length(max=120)])
    timezone = SelectField("Time zone", validators=[DataRequired()])
