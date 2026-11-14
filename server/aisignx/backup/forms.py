from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileRequired
from wtforms import BooleanField, IntegerField, StringField
from wtforms.validators import DataRequired, InputRequired, NumberRange


class CreateForm(FlaskForm):
    include_uploads = BooleanField("Include uploaded media")


class UploadForm(FlaskForm):
    file = FileField("Backup archive (.zip)", validators=[FileRequired()])


class ActionForm(FlaskForm):
    """CSRF only (delete)."""


class RestoreForm(FlaskForm):
    confirm = StringField("Type the file name to confirm", validators=[DataRequired()])


class BackupSettingsForm(FlaskForm):
    enabled = BooleanField("Back up automatically every day")
    hour = IntegerField("At or after this hour (0-23, organization time)",
                        validators=[InputRequired(), NumberRange(min=0, max=23)])
    keep = IntegerField("Keep this many daily backups (1-100)",
                        validators=[InputRequired(), NumberRange(min=1, max=100)])
    include_uploads = BooleanField("Include uploaded media in daily backups")
