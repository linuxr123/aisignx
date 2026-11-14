from flask_wtf import FlaskForm
from wtforms import BooleanField, IntegerField
from wtforms.validators import InputRequired, NumberRange


class PopSettingsForm(FlaskForm):
    enabled = BooleanField("Collect proof of play from screens")
    raw_days = IntegerField(
        "Keep individual plays for (days)",
        validators=[InputRequired(), NumberRange(min=1, max=90)],
    )
    daily_days = IntegerField(
        "Keep daily totals for (days)",
        validators=[InputRequired(), NumberRange(min=30, max=3650)],
    )
