"""Screens (displays) and their short-lived pairing codes."""
from datetime import datetime, timedelta, timezone

from ..extensions import db
from ..timeutil import as_utc

STATE_UNPAIRED = "unpaired"
STATE_NEVER = "never"
STATE_ONLINE = "online"
STATE_OFFLINE = "offline"


def _utcnow():
    return datetime.now(timezone.utc)


class Display(db.Model):
    """A paired screen. last_seen_at is the only source of online state.

    There is deliberately no updated_at: heartbeats would bump it constantly.
    """

    __tablename__ = "displays"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    # Default playlist: plays when no schedule matches.
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id", ondelete="SET NULL"),
                            nullable=True, index=True)
    group_id = db.Column(db.Integer,
                         db.ForeignKey("display_groups.id", ondelete="SET NULL",
                                       name="fk_displays_group_id_display_groups"),
                         nullable=True, index=True)

    # sha256 hex of the raw display token. NULL means unpaired or revoked.
    token_hash = db.Column(db.String(64), nullable=True, unique=True, index=True)
    paired_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Reported by the player in heartbeats.
    last_seen_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_ip = db.Column(db.String(45), nullable=True)
    client_id = db.Column(db.String(64), nullable=True)
    player_version = db.Column(db.String(32), nullable=True)
    reported_version = db.Column(db.String(16), nullable=True)
    current_item_id = db.Column(db.Integer, nullable=True)  # no FK: items get deleted
    screen_width = db.Column(db.Integer, nullable=True)
    screen_height = db.Column(db.Integer, nullable=True)
    device_info = db.Column(db.String(120), nullable=True)

    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    playlist = db.relationship("Playlist")
    group = db.relationship("DisplayGroup", back_populates="displays")

    @property
    def is_paired(self) -> bool:
        return self.token_hash is not None

    def state(self, now: datetime, offline_after: timedelta) -> str:
        """One of "unpaired", "never", "online" or "offline"."""
        if self.token_hash is None:
            return STATE_UNPAIRED
        if self.last_seen_at is None:
            return STATE_NEVER
        if as_utc(now) - as_utc(self.last_seen_at) < offline_after:
            return STATE_ONLINE
        return STATE_OFFLINE

    def __repr__(self) -> str:
        return f"<Display {self.id} {self.name!r}>"


class PairingCode(db.Model):
    """A code shown on an unpaired screen. Deleted when claimed, so single-use.

    display_id NULL means pending; set means approved and waiting for the device's claim.
    """

    __tablename__ = "pairing_codes"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(8), nullable=False, unique=True, index=True)
    secret_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    client_ip = db.Column(db.String(45), nullable=True)
    client_id = db.Column(db.String(64), nullable=True)
    device_info = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    display_id = db.Column(db.Integer, db.ForeignKey("displays.id", ondelete="CASCADE"),
                           nullable=True)

    def __repr__(self) -> str:
        return f"<PairingCode {self.id} {self.code} display={self.display_id}>"
