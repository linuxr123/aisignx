"""Forms for the audit log viewer: the GET filter bar and the retention setting."""
import re

from flask_wtf import FlaskForm
from wtforms import DateField, IntegerField, SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import InputRequired, Length, NumberRange, Optional, ValidationError

from .service import ACTIONS

TARGET_RE = re.compile(r"^([a-z_]{1,32})(?::(\d{1,10}))?$")


def action_choices() -> list[SelectChoice]:
    choices = [SelectChoice("", "All actions")]
    for key, label in ACTIONS.items():
        choices.append(SelectChoice(key, label if key.endswith(".") else f"- {label}"))
    return choices


class AuditFilterForm(FlaskForm):
    """Read from the query string (no CSRF: it only filters a GET page)."""

    class Meta:
        csrf = False

    user = StringField("User", validators=[Optional(), Length(max=64)],
                       render_kw={"placeholder": "username", "list": "audit-users",
                                  "autocomplete": "off"})
    action = SelectField("Action", validators=[Optional()])
    target = StringField("Target", validators=[Optional(), Length(max=48)],
                         render_kw={"placeholder": "e.g. display:12"})
    date_from = DateField("From", validators=[Optional()])
    date_to = DateField("To", validators=[Optional()])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.action.choices = action_choices()

    def validate_target(self, field) -> None:
        if field.data and not TARGET_RE.match(field.data.strip().lower()):
            raise ValidationError('Use "type" or "type:id", e.g. display:12.')

    def validate_date_to(self, field) -> None:
        if field.data and self.date_from.data and field.data < self.date_from.data:
            raise ValidationError("The end date is before the start date.")


class RetentionForm(FlaskForm):
    # InputRequired, not DataRequired: 0 (keep everything) is a valid answer.
    retention_days = IntegerField(
        "Keep audit entries for (days)",
        validators=[InputRequired(message="Enter a number (0 keeps everything)."),
                    NumberRange(min=0, max=3650)],
        description="0 keeps everything. Older entries are deleted every few hours.",
    )
