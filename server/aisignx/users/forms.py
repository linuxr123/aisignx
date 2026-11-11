from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Email, Length, Optional, Regexp

from ..models import ROLES

MIN_PASSWORD_LENGTH = 10

USERNAME_RULES = [
    DataRequired(),
    Length(min=3, max=64),
    Regexp(r"^[A-Za-z0-9_.-]+$", message="Use letters, digits, dot, dash or underscore."),
]
PASSWORD_RULES = [DataRequired(), Length(min=MIN_PASSWORD_LENGTH, max=256)]
ROLE_CHOICES = [SelectChoice(r, r.capitalize()) for r in ROLES]


def optional_email():
    return [Optional(), Email(check_deliverability=False), Length(max=255)]


class UserCreateForm(FlaskForm):
    username = StringField("Username", validators=USERNAME_RULES)
    email = StringField("Email (optional)", validators=optional_email())
    role = SelectField("Role", choices=ROLE_CHOICES, default="viewer")
    password = PasswordField("Password", validators=PASSWORD_RULES)


class UserEditForm(FlaskForm):
    email = StringField("Email (optional)", validators=optional_email())
    role = SelectField("Role", choices=ROLE_CHOICES)
    active = BooleanField("Account active")
    password = PasswordField(
        "New password (leave blank to keep)",
        validators=[Optional(), Length(min=MIN_PASSWORD_LENGTH, max=256)],
    )


class DeleteForm(FlaskForm):
    """Empty form used only for its CSRF token."""
