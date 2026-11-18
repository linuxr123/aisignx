from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import Length, Optional

from ..models import DOWNLOAD_KINDS


class UploadForm(FlaskForm):
    file = FileField("File", validators=[FileRequired()])
    kind = SelectField("What is it?", choices=[SelectChoice("auto", "Detect automatically (recommended)")] +
                      [SelectChoice(k, v) for k, v in DOWNLOAD_KINDS.items()], default="auto")
    version = StringField("Version", validators=[Optional(), Length(max=32)])
    notes = StringField("Notes", validators=[Optional(), Length(max=400)])


class ActionForm(FlaskForm):
    """Empty form: CSRF only (delete)."""
