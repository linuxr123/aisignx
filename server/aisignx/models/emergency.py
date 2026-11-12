"""Emergency messages: a manual override shown on top of (or instead of) normal content.

Live while cleared_at is NULL. The explicit scope column means that once a target is
deleted (display_id/group_id SET NULL) the message matches no screen, never all screens.
"""
from datetime import datetime, timezone

from ..extensions import db
from ..timeutil import iso_z

LEVELS = ("info", "warning", "critical")
LEVEL_RANK = {"info": 0, "warning": 1, "critical": 2}
# (background, foreground) per level.
LEVEL_COLORS = {
    "info": ("#1565c0", "#ffffff"),
    "warning": ("#e65100", "#ffffff"),
    "critical": ("#b71c1c", "#ffffff"),
}
SCOPES = ("all", "group", "display")
CLEAR_MANUAL = "manual"
CLEAR_TARGET_DELETED = "target_deleted"
LAYOUTS = ("fullscreen", "banner", "none")


def _utcnow():
    return datetime.now(timezone.utc)


class EmergencyMessage(db.Model):
    __tablename__ = "emergency_messages"
    __table_args__ = (
        db.CheckConstraint("level IN ('info', 'warning', 'critical')", name="ck_emergency_level"),
        db.CheckConstraint("scope IN ('all', 'group', 'display')", name="ck_emergency_scope"),
        db.CheckConstraint("scope <> 'all' OR (display_id IS NULL AND group_id IS NULL)",
                           name="ck_emergency_all"),
        db.CheckConstraint("NOT (display_id IS NOT NULL AND group_id IS NOT NULL)",
                           name="ck_emergency_one"),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False, default="")
    level = db.Column(db.String(10), nullable=False, default="critical")
    # False is only allowed together with a playlist (form rule).
    show_text = db.Column(db.Boolean, nullable=False, default=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id", ondelete="SET NULL"),
                            nullable=True, index=True)
    scope = db.Column(db.String(10), nullable=False)
    display_id = db.Column(db.Integer, db.ForeignKey("displays.id", ondelete="SET NULL"),
                           nullable=True, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("display_groups.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    # Snapshot for history: "All screens", "Group Lobby", "Display Front desk".
    target_label = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"),
                              nullable=True)
    cleared_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)
    cleared_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"),
                              nullable=True)
    clear_reason = db.Column(db.String(20), nullable=True)  # manual | target_deleted

    playlist = db.relationship("Playlist")
    display = db.relationship("Display")
    group = db.relationship("DisplayGroup")
    created_by = db.relationship("User", foreign_keys=[created_by_id])
    cleared_by = db.relationship("User", foreign_keys=[cleared_by_id])

    @property
    def live(self) -> bool:
        return self.cleared_at is None

    @property
    def colors(self) -> tuple[str, str]:
        return LEVEL_COLORS.get(self.level, LEVEL_COLORS["critical"])

    def to_player(self, layout: str) -> dict:
        """The manifest `emergency` object (also the SSE `emergency` event payload)."""
        background, foreground = self.colors
        return {
            "id": self.id,
            "title": self.title,
            "message": self.message or "",
            "level": self.level,
            "background": background,
            "foreground": foreground,
            "layout": layout,
            "since": iso_z(self.created_at),
        }

    def __repr__(self) -> str:
        return f"<EmergencyMessage {self.id} {self.level} {self.title!r}>"
