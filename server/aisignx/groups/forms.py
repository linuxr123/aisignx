from flask_wtf import FlaskForm
from sqlalchemy import func
from wtforms import SelectMultipleField, StringField, TextAreaField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, Optional, ValidationError

from ..models import Display, DisplayGroup


class GroupForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=100)],
                       render_kw={"placeholder": "e.g. Lobby screens"})
    description = TextAreaField("Description", validators=[Optional(), Length(max=255)],
                                render_kw={"rows": 2})
    # Rendered as a checkbox list in groups/form.html.
    members = SelectMultipleField("Members", coerce=int, default=[])

    def __init__(self, *args, group: DisplayGroup | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._group = group
        self.displays = Display.query.order_by(Display.name, Display.id).all()
        self.members.choices = [SelectChoice(d.id, d.name) for d in self.displays]

    def validate_name(self, field) -> None:
        name = (field.data or "").strip()
        query = DisplayGroup.query.filter(func.lower(DisplayGroup.name) == name.lower())
        if self._group is not None and self._group.id is not None:
            query = query.filter(DisplayGroup.id != self._group.id)
        if query.first() is not None:
            raise ValidationError("Another group already has this name.")


class ActionForm(FlaskForm):
    """Empty form used only for its CSRF token."""
