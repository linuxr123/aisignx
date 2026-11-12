"""Display groups: a flat set of screens that schedules and emergencies can target."""
from datetime import datetime, timezone

from ..extensions import db


def _utcnow():
    return datetime.now(timezone.utc)


class DisplayGroup(db.Model):
    """A display belongs to at most one group (displays.group_id, SET NULL on delete)."""

    __tablename__ = "display_groups"

    id = db.Column(db.Integer, primary_key=True)
    # Unique as stored; the form also checks case-insensitive uniqueness.
    name = db.Column(db.String(100), nullable=False, unique=True, index=True)
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    displays = db.relationship("Display", back_populates="group", passive_deletes=True,
                               order_by="Display.name")

    def __repr__(self) -> str:
        return f"<DisplayGroup {self.id} {self.name!r}>"
