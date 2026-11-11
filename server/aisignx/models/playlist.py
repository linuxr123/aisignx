"""Playlists and their ordered items."""
from datetime import datetime, timezone

from ..extensions import db

# Transitions the player understands. "cut" means no animation.
TRANSITIONS = (
    "cut", "fade", "crossfade", "wipe",
    "slide-left", "slide-right", "slide-up", "slide-down", "zoom",
)
# The playlist-wide default may also be "random" (the player picks per slide).
DEFAULT_TRANSITIONS = TRANSITIONS + ("random",)

# How media fills the screen.
FIT_MODES = ("fit", "fill", "stretch")


def _utcnow():
    return datetime.now(timezone.utc)


class Playlist(db.Model):
    __tablename__ = "playlists"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    default_transition = db.Column(db.String(16), nullable=False, default="fade")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow,
                           onupdate=_utcnow)

    items = db.relationship("PlaylistItem", back_populates="playlist",
                            order_by="PlaylistItem.position",
                            cascade="all, delete-orphan", passive_deletes=True)

    @property
    def total_duration(self) -> int:
        return sum(item.effective_duration for item in self.items)

    def __repr__(self) -> str:
        return f"<Playlist {self.id} {self.name!r}>"


class PlaylistItem(db.Model):
    __tablename__ = "playlist_items"

    id = db.Column(db.Integer, primary_key=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    media_id = db.Column(db.Integer, db.ForeignKey("media.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    position = db.Column(db.Integer, nullable=False, default=0)

    # Overrides; NULL means "use the media / playlist default".
    duration = db.Column(db.Integer, nullable=True)
    transition = db.Column(db.String(16), nullable=True)
    fit_mode = db.Column(db.String(16), nullable=False, default="fit")
    mute = db.Column(db.Boolean, nullable=False, default=True)

    playlist = db.relationship("Playlist", back_populates="items")
    media = db.relationship("Media", back_populates="playlist_items")

    @property
    def effective_duration(self) -> int:
        if self.duration and self.duration > 0:
            return self.duration
        return self.media.effective_duration

    @property
    def effective_transition(self) -> str:
        return self.transition or self.playlist.default_transition or "cut"
