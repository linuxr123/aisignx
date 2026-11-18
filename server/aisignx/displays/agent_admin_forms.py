"""Form for the automatic sign-in request. Field values are read in agent_admin.py and never echoed back."""
from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField


class AutologinForm(FlaskForm):
    user = StringField("Windows account")
    password = PasswordField("Windows password")
    confirm = PasswordField("Your AiSignX password")
