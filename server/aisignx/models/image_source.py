"""A place images are read from (a folder, a share, a cloud album) and kept in step with the media library.

The sync job (aisignx/sources/sync.py) imports each image as an ordinary Media row marked with `source_id`, so
thumbnails, checksums, the player's caches and playlists work as for uploads. Only the source's settings live here;
a password or API key is stored encrypted (like plugin secrets), never in `settings`.
"""
from ..extensions import db
from ..timeutil import utcnow

KINDS = ("folder", "http", "webdav", "gdrive", "onedrive", "dropbox", "flickr")


class ImageSource(db.Model):
    __tablename__ = "image_sources"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    kind = db.Column(db.String(12), nullable=False)
    location = db.Column(db.String(1024), nullable=False)          # folder path, URL, link or id (see sources/adapters)
    username = db.Column(db.String(120), nullable=False, default="")
    secret = db.Column(db.Text, nullable=True)                      # password / API key, encrypted
    recursive = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    mirror = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())   # remove images that left the source
    enabled = db.Column(db.Boolean, nullable=False, default=True, server_default=db.true())
    interval_min = db.Column(db.Integer, nullable=False, default=60, server_default="60")
    max_images = db.Column(db.Integer, nullable=False, default=200, server_default="200")
    next_sync_at = db.Column(db.DateTime(timezone=True), nullable=True, index=True)   # NULL: not yet; past: due
    last_sync_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_status = db.Column(db.String(300), nullable=False, default="", server_default="")   # one line for the admin page
    last_ok = db.Column(db.Boolean, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
