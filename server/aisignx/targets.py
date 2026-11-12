"""Who a schedule or emergency message is for: all screens, one group or one display.

Form values: "all", "g:<group id>", "d:<display id>". Objects carry the parsed form as
(scope, display_id, group_id) columns; see Schedule and EmergencyMessage.
"""
import re

from wtforms.fields.choices import SelectChoice
from wtforms.validators import ValidationError

from .extensions import db
from .models import Display, DisplayGroup

ALL = "all"
ALL_LABEL = "All screens"
# Ids are capped at 18 digits so a hand-made value can't overflow the DB integer type.
_VALUE = re.compile(r"^(all|g:\d{1,18}|d:\d{1,18})$")


def choices(placeholder: bool = False) -> list[SelectChoice]:
    """Select options, with groups and displays in their own optgroups.

    placeholder=True puts an empty "Choose screens…" option first, so a browser never
    silently submits "All screens" when nothing was chosen (used for emergencies).
    """
    out = [SelectChoice("", "Choose screens…")] if placeholder else []
    out.append(SelectChoice(ALL, ALL_LABEL))
    for g in DisplayGroup.query.order_by(DisplayGroup.name).all():
        out.append(SelectChoice(f"g:{g.id}", f"Group: {g.name}", optgroup="Groups"))
    for d in Display.query.order_by(Display.name, Display.id).all():
        out.append(SelectChoice(f"d:{d.id}", f"Display: {d.name}", optgroup="Displays"))
    return out


def parse(value) -> tuple[str, int | None, int | None]:
    """"all" | "g:<id>" | "d:<id>" -> (scope, display_id, group_id).

    Raises ValidationError for a malformed value or an id that doesn't exist.
    """
    value = (value or "").strip() if isinstance(value, str) else ""
    if not _VALUE.match(value):
        raise ValidationError("Choose who this is for.")
    if value == ALL:
        return "all", None, None
    kind, raw = value.split(":", 1)
    ident = int(raw)
    if kind == "g":
        if db.session.get(DisplayGroup, ident) is None:
            raise ValidationError("That group no longer exists.")
        return "group", None, ident
    if db.session.get(Display, ident) is None:
        raise ValidationError("That display no longer exists.")
    return "display", ident, None


def encode(obj) -> str:
    """The form value for an object's target."""
    if obj.scope == "group" and obj.group_id is not None:
        return f"g:{obj.group_id}"
    if obj.scope == "display" and obj.display_id is not None:
        return f"d:{obj.display_id}"
    return ALL


def label(obj) -> str:
    """Human text: "All screens", "Group Lobby", "Display Front desk"."""
    # get() rather than the relationship: a new, unflushed object has only the id set.
    if obj.scope == "group":
        group = db.session.get(DisplayGroup, obj.group_id) if obj.group_id else None
        return f"Group {group.name}" if group is not None else "Group (deleted)"
    if obj.scope == "display":
        display = db.session.get(Display, obj.display_id) if obj.display_id else None
        return f"Display {display.name}" if display is not None else "Display (deleted)"
    return ALL_LABEL


def applies(obj, display) -> bool:
    """Whether a schedule or emergency message targets this display."""
    return (obj.scope == "all"
            or (obj.scope == "display" and obj.display_id == display.id)
            or (obj.scope == "group" and obj.group_id is not None
                and obj.group_id == display.group_id))
