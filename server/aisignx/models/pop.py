"""Proof of play: raw play events from screens and their daily rollup.

PlayEvent rows are kept for `pop.raw_days`; reports read PlayDaily (`pop.daily_days`).
Neither table is watched for live updates.
"""
from ..extensions import db
from ..timeutil import utcnow


class PlayEvent(db.Model):
    __tablename__ = "play_events"

    id = db.Column(db.Integer, primary_key=True)
    display_id = db.Column(db.Integer, db.ForeignKey("displays.id", ondelete="CASCADE"),
                           nullable=False)
    client_event_id = db.Column(db.String(64), nullable=False)  # dedupes resends
    media_id = db.Column(db.Integer, nullable=False)  # no FK: media gets deleted
    item_id = db.Column(db.Integer, nullable=True)
    media_name = db.Column(db.String(200), nullable=True)
    started_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    duration_ms = db.Column(db.Integer, nullable=False)
    received_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True,
                            default=utcnow)

    __table_args__ = (
        db.UniqueConstraint("display_id", "client_event_id",
                            name="uq_play_events_display_client"),
        db.Index("ix_play_events_media_started", "media_id", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<PlayEvent {self.id} display={self.display_id} media={self.media_id}>"


class PlayDaily(db.Model):
    """Plays per local day (organization time zone), display and media."""

    __tablename__ = "play_daily"

    day = db.Column(db.Date, primary_key=True)
    display_id = db.Column(db.Integer, primary_key=True, autoincrement=False)  # no FK
    media_id = db.Column(db.Integer, primary_key=True, autoincrement=False)
    display_name = db.Column(db.String(120), nullable=True)
    media_name = db.Column(db.String(200), nullable=True)
    plays = db.Column(db.Integer, nullable=False, default=0)
    seconds = db.Column(db.Integer, nullable=False, default=0)

    def __repr__(self) -> str:
        return f"<PlayDaily {self.day} display={self.display_id} media={self.media_id}>"
