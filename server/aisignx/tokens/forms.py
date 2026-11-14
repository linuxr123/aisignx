from flask_wtf import FlaskForm
from wtforms import SelectField, SelectMultipleField, StringField, widgets
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length

EXPIRY_LABELS = [SelectChoice("never", "Never"), SelectChoice("30", "30 days"),
                 SelectChoice("90", "90 days"), SelectChoice("365", "1 year")]


class CheckboxListField(SelectMultipleField):
    widget = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class TokenForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=80)],
                       description='What uses it, for example "PowerPoint, meeting room".')
    scopes = CheckboxListField("Permissions", choices=[])
    expires = SelectField("Expires", choices=EXPIRY_LABELS, default="90")

    def expires_days(self) -> int | None:
        return None if self.expires.data == "never" else int(self.expires.data)


class RevokeForm(FlaskForm):
    """Empty form used only for its CSRF token."""
