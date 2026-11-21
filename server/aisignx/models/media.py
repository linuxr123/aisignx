"""Media library items: uploaded images and videos, web pages by URL, and plugin items
(a bundled plugin plus its validated config; see aisignx/plugins)."""
import math
from datetime import datetime, timezone

from ..extensions import db

MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_WEB = "web"
MEDIA_PLUGIN = "plugin"
MEDIA_TYPES = (MEDIA_IMAGE, MEDIA_VIDEO, MEDIA_WEB, MEDIA_PLUGIN)

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

    # Web pages. Many sites forbid being shown inside another page (X-Frame-Options, CSP
    # frame-ancestors). web_mode: "auto" shows the live page unless the site forbids it (then a
    # screenshot), "live" always embeds it, "screenshot" always shows a screenshot that the
    # server retakes every web_refresh_s seconds (media/webpage.py).
    url = db.Column(db.String(2048), nullable=True)
    web_mode = db.Column(db.String(12), nullable=False, default="auto", server_default="auto")
    web_refresh_s = db.Column(db.Integer, nullable=False, default=300, server_default="300")
    web_embeddable = db.Column(db.Boolean, nullable=True)  # NULL: not checked yet
    web_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    snapshot_name = db.Column(db.String(64), nullable=True)  # PNG in the media folder
    snapshot_sha256 = db.Column(db.String(64), nullable=True)
    snapshot_size = db.Column(db.BigInteger, nullable=True)
    snapshot_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Imported from an image source (aisignx/sources): which one, the item's id there, and a change stamp (etag, size and time).
    # No foreign key: deleting a source removes its images in code, so a half-deleted source never blocks anything.
    source_id = db.Column(db.Integer, nullable=True, index=True)
    source_key = db.Column(db.String(700), nullable=True)
    source_stamp = db.Column(db.String(160), nullable=True)

    # Detected metadata.
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)  # videos only
    # Videos: codec, profile, level, pix_fmt, fps, bitrate from ffprobe (media/probe.py). NULL
    # until probed; {} when the file could not be probed.
    video_info = db.Column(db.JSON(none_as_null=True), nullable=True)

    # Plugin items: the registry key and the validated config (always assign a new dict;
    # in-place changes to a JSON column are not detected).
    plugin_key = db.Column(db.String(32), nullable=True, index=True)
    plugin_config = db.Column(db.JSON, nullable=True)

    # How long images, web pages and plugins show by default (seconds).
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
    def is_plugin(self) -> bool:
        return self.media_type == MEDIA_PLUGIN

    @property
    def has_sound(self) -> bool:
        """Videos and plugins with sound (YouTube): the playlist sets mute and volume."""
        if self.is_video:
            return True
        if not self.is_plugin:
            return False
        from ..plugins.registry import get_plugin

        plugin = get_plugin(self.plugin_key or "")
        return bool(plugin and plugin.audio)

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

    @property
    def shows_snapshot(self) -> bool:
        """A web page that plays as a screenshot: chosen, or the site forbids embedding."""
        return (self.media_type == MEDIA_WEB
                and (self.web_mode == "screenshot"
                     or (self.web_mode in (None, "auto") and self.web_embeddable is False)))

    @property
    def playback_warnings(self) -> list[str]:
        """Why this video may not play smoothly on a TV box (media/compat.py); empty if fine
        or not yet probed."""
        if not self.is_video or not self.video_info:
            return []
        from ..media import compat

        return compat.warnings(self.video_info, self.width, self.height)

    @property
    def video_summary(self) -> str | None:
        """"H.264 High · 25 fps · 41 Mbps" for the edit page; None when unknown."""
        info = self.video_info or {}
        if not info.get("codec"):
            return None
        parts = [str(info["codec"]).upper() + (f" {info['profile']}" if info.get("profile") else "")]
        if info.get("fps"):
            parts.append(f"{info['fps']:g} fps")
        if info.get("bitrate"):
            parts.append(f"{info['bitrate'] / 1_000_000:.1f} Mbps")
        return " · ".join(parts)

    def __repr__(self) -> str:
        return f"<Media {self.id} {self.media_type} {self.name!r}>"
