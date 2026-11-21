"""A scheduled emergency: send a saved message at a set time, optionally clearing it after a while."""
from ..extensions import db
from ..timeutil import utcnow

STATES = ("pending", "fired", "cancelled", "missed", "failed")
REPEATS = ("none", "daily", "weekdays", "weekly")


class EmergencyPlan(db.Model):
    __tablename__ = "emergency_plans"

    id = db.Column(db.Integer, primary_key=True)
    template_id = db.Column(db.Integer, db.ForeignKey("emergency_templates.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    starts_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    duration_min = db.Column(db.Integer, nullable=True)  # None: stays up until someone clears it
    repeat = db.Column(db.String(10), nullable=False, default="none", server_default="none")  # a new plan follows each one
    state = db.Column(db.String(12), nullable=False, default="pending", index=True)
    note = db.Column(db.String(200), nullable=False, default="")  # why a plan was missed or failed
    message_id = db.Column(db.Integer, db.ForeignKey("emergency_messages.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    template = db.relationship("EmergencyTemplate")
    created_by = db.relationship("User")
