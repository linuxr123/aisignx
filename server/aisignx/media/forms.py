from urllib.parse import urlsplit

from flask_wtf import FlaskForm
from flask_wtf.file import MultipleFileField
from wtforms import IntegerField, StringField, ValidationError
from wtforms.validators import DataRequired, Length, NumberRange, Optional

MAX_DURATION = 24 * 3600


def http_url(_form, field):
    parts = urlsplit((field.data or "").strip())
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValidationError("Enter a full http:// or https:// address.")


class UploadForm(FlaskForm):
    files = MultipleFileField("Files")


class WebPageForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    url = StringField("Address (URL)", validators=[DataRequired(), Length(max=2048), http_url])
    duration = IntegerField("Show for (seconds)",
                            validators=[Optional(), NumberRange(min=1, max=MAX_DURATION)])


class MediaEditForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=200)])
    url = StringField("Address (URL)", validators=[Optional(), Length(max=2048), http_url])
    default_duration = IntegerField("Default duration (seconds)",
                                    validators=[DataRequired(), NumberRange(min=1, max=MAX_DURATION)])


class DeleteForm(FlaskForm):
    """Empty form used only for its CSRF token."""
