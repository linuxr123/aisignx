"""An installer or add-in file offered on the Downloads page (admins upload, everyone signed in downloads)."""
from ..extensions import db
from ..timeutil import utcnow

# kind -> label shown on the page, in display order
KINDS = {
    "windows-player": "Windows player app",
    "android-player": "Android player app (APK)",
    "powerpoint-addin": "PowerPoint add-in",
    "other": "Other",
}


class Download(db.Model):
    __tablename__ = "downloads"

    id = db.Column(db.Integer, primary_key=True)
    kind = db.Column(db.String(24), nullable=False, index=True)
    version = db.Column(db.String(32), nullable=False, default="")
    filename = db.Column(db.String(160), nullable=False)      # name offered to the browser
    stored_name = db.Column(db.String(64), nullable=False, unique=True)  # random, on disk
    size = db.Column(db.BigInteger, nullable=False)
    sha256 = db.Column(db.String(64), nullable=False)
    notes = db.Column(db.String(400), nullable=False, default="")
    uploaded_by = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    download_count = db.Column(db.Integer, nullable=False, default=0)
