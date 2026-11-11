from flask_wtf import FlaskForm
from wtforms import PasswordField, SelectField, StringField
from wtforms.validators import DataRequired, EqualTo, Length

from ..users.forms import PASSWORD_RULES, USERNAME_RULES, optional_email


class SetupForm(FlaskForm):
    org_name = StringField("Organization name", validators=[DataRequired(), Length(max=120)])
    timezone = SelectField("Time zone", validators=[DataRequired()])
    username = StringField("Admin username", validators=USERNAME_RULES)
    email = StringField("Email (optional)", validators=optional_email())
    password = PasswordField("Password", validators=PASSWORD_RULES)
    confirm = PasswordField(
        "Confirm password",
        validators=[DataRequired(), EqualTo("password", message="Passwords must match.")],
    )
