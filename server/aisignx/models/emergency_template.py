"""A saved emergency message ("Fire drill", "Lockdown") that can be sent in one click or from the API."""
from ..extensions import db
from ..timeutil import utcnow


class EmergencyTemplate(db.Model):
    __tablename__ = "emergency_templates"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), nullable=False, unique=True)
    title = db.Column(db.String(120), nullable=False)
    message = db.Column(db.Text, nullable=False, default="")
    level = db.Column(db.String(10), nullable=False, default="critical")
    show_text = db.Column(db.Boolean, nullable=False, default=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey("playlists.id", ondelete="SET NULL"), nullable=True)
    # targets form value: "all", "g:<id>" or "d:<id>". If that group or display is deleted the template
    # refuses to send (it never widens to all screens).
    target = db.Column(db.String(24), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    playlist = db.relationship("Playlist")
    created_by = db.relationship("User")
