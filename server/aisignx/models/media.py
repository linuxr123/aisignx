"""Media library items: uploaded images and videos, and web pages by URL."""
import math
from datetime import datetime, timezone

from ..extensions import db

MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_WEB = "web"
MEDIA_TYPES = (MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_WEB)

DEFAULT_DURATION = 10  # seconds, for images and web pages

# Thumbnail states. "failed" may still have a fallback image (e.g. a title card).
THUMB_OK = "ok"
THUMB_PENDING = "pending"
THUMB_FAILED = "failed"


def _utcnow():
    return datetime.now(timezone.utc)


class Media(db.Model):
    __tablename__ = "media"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    media_type = db.Column(db.String(16), nullable=False, index=True)

    # Uploaded files: original name for display, random stored name on disk.
    original_filename = db.Column(db.String(255), nullable=True)
    stored_name = db.Column(db.String(64), nullable=True, unique=True)
    mime_type = db.Column(db.String(100), nullable=True)
    file_size = db.Column(db.BigInteger, nullable=True)
    checksum_sha256 = db.Column(db.String(64), nullable=True, index=True)

    # Web pages.
    url = db.Column(db.String(2048), nullable=True)

    # Detected metadata.
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)  # videos only

    # How long images and web pages show by default (seconds).
    default_duration = db.Column(db.Integer, nullable=False, default=DEFAULT_DURATION)

    thumbnail_name = db.Column(db.String(64), nullable=True)
    thumbnail_status = db.Column(db.String(16), nullable=True)
    uploaded_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"),
                               nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    uploaded_by = db.relationship("User")
    playlist_items = db.relationship("PlaylistItem", back_populates="media",
                                     cascade="all, delete-orphan", passive_deletes=True)

    @property
    def is_video(self) -> bool:
        return self.media_type == MEDIA_VIDEO

    @property
    def effective_duration(self) -> int:
        """Seconds this media plays for when a playlist item doesn't override it."""
        if self.is_video and self.duration_seconds:
            return max(1, math.ceil(self.duration_seconds))
        return self.default_duration or DEFAULT_DURATION

    @property
    def resolution(self) -> str | None:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return None

    def __repr__(self) -> str:
        return f"<Media {self.id} {self.media_type} {self.name!r}>"
