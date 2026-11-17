"""Commands for a display's device agent (restart the player, collect logs, ...).

The database row is the truth; SSE and the heartbeat only tell the device to look. A command
is created by an admin, has a short life (judged with SERVER time: cheap devices have wrong
clocks), is fetched and acknowledged by the display it belongs to, and runs at most once.
See docs/device-agent-design.md.
"""
from datetime import datetime, timezone

from ..extensions import db

# A closed list of verbs. Parameters are typed and validated in displays/agent.py; a command is
# never a command line, a path or a URL.
ACTION_RESTART_PLAYER = "restart_player"
ACTION_COLLECT_LOGS = "collect_logs"
# Tier B: can take a screen out of service or run new code, so each is signed by the server's command key
# and checked on the device (docs/signed-commands.md).
ACTION_REBOOT = "reboot_device"
ACTION_SET_STARTUP = "set_startup"
ACTION_UPDATE_APP = "update_app"
SIGNED_ACTIONS = (ACTION_REBOOT, ACTION_SET_STARTUP, ACTION_UPDATE_APP)
ACTIONS = (ACTION_RESTART_PLAYER, ACTION_COLLECT_LOGS) + SIGNED_ACTIONS

STATE_PENDING = "pending"        # created, not yet fetched by the device
STATE_DELIVERED = "delivered"    # the device fetched it
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_EXPIRED = "expired"
STATE_CANCELLED = "cancelled"
OPEN_STATES = (STATE_PENDING, STATE_DELIVERED)
STATES = (STATE_PENDING, STATE_DELIVERED, STATE_DONE, STATE_FAILED, STATE_EXPIRED, STATE_CANCELLED)


def _utcnow():
    return datetime.now(timezone.utc)


class DeviceCommand(db.Model):
    __tablename__ = "device_commands"

    id = db.Column(db.Integer, primary_key=True)
    display_id = db.Column(db.Integer, db.ForeignKey("displays.id", ondelete="CASCADE"),
                           nullable=False, index=True)
    # 16 hex chars, unguessable: the device quotes it when it acknowledges or uploads.
    request_id = db.Column(db.String(32), nullable=False, unique=True)
    action = db.Column(db.String(32), nullable=False)
    params = db.Column(db.JSON, nullable=True)
    state = db.Column(db.String(16), nullable=False, default=STATE_PENDING, index=True)
    result = db.Column(db.String(200), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"),
                              nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    delivered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    acked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    display = db.relationship("Display", backref=db.backref("device_commands", passive_deletes=True))

    def __repr__(self) -> str:
        return f"<DeviceCommand {self.id} {self.action} {self.state}>"
