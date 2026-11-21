"""A privileged agent that was installed on a PC and asks to be enrolled (docs/windows-agent-service.md, "Approval").

The agent knows two random values: the claim id (it also writes it where the player can read it) and the poll
secret (only the agent knows it). The player, signed in as its display, sends the claim id to the server: that ties
the PC's agent to that display. An admin approves; the agent token is then encrypted to the agent's own public key and
waits here until the agent collects it with the poll secret. Only hashes of both values are stored.
"""
from ..extensions import db
from ..timeutil import utcnow

STATES = ("pending", "approved", "rejected")


class AgentRequest(db.Model):
    __tablename__ = "agent_requests"

    id = db.Column(db.Integer, primary_key=True)
    claim_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    poll_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    public_key = db.Column(db.String(64), nullable=False)       # base64 X25519, 32 bytes
    host = db.Column(db.String(64), nullable=False, default="")
    version = db.Column(db.String(20), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False, index=True)
    display_id = db.Column(db.Integer, db.ForeignKey("displays.id", ondelete="CASCADE"), nullable=True, index=True)
    state = db.Column(db.String(10), nullable=False, default="pending")
    ciphertext = db.Column(db.Text, nullable=True)               # the agent token, encrypted to public_key

    display = db.relationship("Display")
