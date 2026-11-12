"""Schedules: play a playlist on some screens during a weekly time window.

Rules (see docs/scheduling.md): days_mask bit i is date.weekday() i (bit0 = Mon);
start_time/end_time are both NULL (all day) or both set; end < start is an overnight
window that ends the next day; the weekday and date range apply to the day an
occurrence starts on.
"""
from datetime import datetime, timezone

from ..extensions import db

SCOPES = ("all", "group", "display")
SPECIFICITY = {"display": 2, "group": 1, "all": 0}
ALL_DAYS = 0b1111111
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
PRIORITY_MAX = 100


def _utcnow():
    return datetime.now(timezone.utc)


def days_label(mask: int) -> str:
    """"Every day", "Mon–Fri", "Sat, Sun", "Mon, Wed–Fri"."""
    mask = int(mask or 0) & ALL_DAYS
    if mask == ALL_DAYS:
        return "Every day"
    if not mask:
        return "Never"
    parts, i = [], 0
    while i < 7:
        if not mask >> i & 1:
            i += 1
            continue
        j = i
        while j + 1 < 7 and mask >> (j + 1) & 1:
            j += 1
        if j - i >= 2:
            parts.append(f"{DAY_NAMES[i]}–{DAY_NAMES[j]}")
        else:
            parts.extend(DAY_NAMES[i:j + 1])
        i = j + 1
    return ", ".join(parts)


class Schedule(db.Model):
    __tablename__ = "schedules"
    __table_args__ = (
        db.CheckConstraint("scope IN ('all', 'group', 'display')", name="ck_schedules_scope"),
        db.CheckConstraint(
            "(scope = 'all' AND display_id IS NULL AND group_id IS NULL) OR "
            "(scope = 'display' AND display_id IS NOT NULL AND group_id IS NULL) OR "
            "(scope = 'group' AND group_id IS NOT NULL AND display_id IS NULL)",
            name="ck_schedules_target"),
        db.CheckConstraint("days_mask BETWEEN 1 AND 127", name="ck_schedules_days"),
        db.CheckConstraint("(start_time IS NULL) = (end_time IS NULL)",
                           name="ck_schedules_time_pair"),
        db.CheckConstraint("start_time IS NULL OR start_time <> end_time",
                           name="ck_schedules_time_ne"),
        db.CheckConstraint("start_date IS NULL OR end_date IS NULL OR end_date >= start_date",
                           name="ck_schedules_dates"),
        db.CheckConstraint("priority BETWEEN 0 AND 100", name="ck_schedules_priority"),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id", ondelete="RESTRICT"),
                            nullable=False, index=True)
    scope = db.Column(db.String(10), nullable=False)
    display_id = db.Column(db.Integer, db.ForeignKey("displays.id", ondelete="CASCADE"),
                           nullable=True, index=True)
    group_id = db.Column(db.Integer, db.ForeignKey("display_groups.id", ondelete="CASCADE"),
                         nullable=True, index=True)
    days_mask = db.Column(db.Integer, nullable=False, default=ALL_DAYS)
    start_time = db.Column(db.Time, nullable=True)
    end_time = db.Column(db.Time, nullable=True)
    start_date = db.Column(db.Date, nullable=True)  # inclusive, owning-day rule
    end_date = db.Column(db.Date, nullable=True)    # inclusive, owning-day rule
    priority = db.Column(db.Integer, nullable=False, default=0)
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow,
                           onupdate=_utcnow)

    playlist = db.relationship("Playlist")
    display = db.relationship("Display")
    group = db.relationship("DisplayGroup")

    @property
    def specificity(self) -> int:
        return SPECIFICITY[self.scope]

    @property
    def is_overnight(self) -> bool:
        return (self.start_time is not None and self.end_time is not None
                and self.end_time < self.start_time)

    @property
    def days_label(self) -> str:
        return days_label(self.days_mask)

    @property
    def time_label(self) -> str:
        if self.start_time is None or self.end_time is None:
            return "All day"
        text = f"{self.start_time:%H:%M} → {self.end_time:%H:%M}"
        return text + " (+1 day)" if self.is_overnight else text

    def __repr__(self) -> str:
        return f"<Schedule {self.id} {self.name!r} {self.scope}>"
