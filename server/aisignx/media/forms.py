from urllib.parse import urlsplit

from flask_wtf import FlaskForm
from flask_wtf.file import FileField, MultipleFileField
from wtforms import BooleanField, IntegerField, SelectField, StringField, ValidationError
from wtforms.validators import DataRequired, Length, NumberRange, Optional

MAX_DURATION = 24 * 3600


def http_url(_form, field):
    parts = urlsplit((field.data or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationError("Enter a full http:// or https:// address.")


class UploadForm(FlaskForm):
    files = MultipleFileField("Files")


WEB_MODE_CHOICES = [
    ("auto", "Automatic: live page, or a screenshot if the site does not allow it"),
    ("live", "Live page"),
    ("screenshot", "Screenshot, refreshed regularly"),
]


class WebPageForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    url = StringField("Address (URL)", validators=[DataRequired(), Length(max=2048), http_url])
    duration = IntegerField("Show for (seconds)",
                            validators=[Optional(), NumberRange(min=1, max=MAX_DURATION)])
    web_mode = SelectField("How to show it", choices=WEB_MODE_CHOICES, default="auto")
    refresh_min = IntegerField("Refresh the screenshot every (minutes)",
                               validators=[Optional(), NumberRange(min=1, max=1440)])


class MediaEditForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    url = StringField("Address (URL)", validators=[Optional(), Length(max=2048), http_url])
    default_duration = IntegerField("Default duration (seconds)",
                                    validators=[DataRequired(), NumberRange(min=1, max=MAX_DURATION)])
    web_mode = SelectField("How to show it", choices=WEB_MODE_CHOICES, default="auto",
                           validate_choice=False)  # absent for images and videos
    refresh_min = IntegerField("Refresh the screenshot every (minutes)",
                               validators=[Optional(), NumberRange(min=1, max=1440)])


class ReplaceForm(FlaskForm):
    file = FileField("New file")


class ConvertForm(FlaskForm):
    """CSRF token and the "replace the original" choice of a TV-friendly conversion."""
    replace = BooleanField("Replace the original")


class DeleteForm(FlaskForm):
    """Empty form used only for its CSRF token."""
