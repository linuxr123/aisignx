"""Screens (displays) and their short-lived pairing codes."""
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa

from ..extensions import db
from ..timeutil import as_utc

STATE_UNPAIRED = "unpaired"
STATE_NEVER = "never"
STATE_ONLINE = "online"
STATE_OFFLINE = "offline"

# Degrees the player rotates its output clockwise.
ORIENTATIONS = (0, 90, 180, 270)
# Seconds after which a paused player resumes on its own.
RESUME_MIN_S = 60
RESUME_MAX_S = 3600
RESUME_DEFAULT_S = 300


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

    # Admin-only notes; never sent to the screen.
    location = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)

    # Per-display options, sent in the manifest's "settings" (see displays/options.py).
    orientation = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    fit_override = db.Column(db.String(16), nullable=True)  # NULL: each item's own fit
    volume = db.Column(db.Integer, nullable=False, default=100, server_default="100")
    show_offline_banner = db.Column(db.Boolean, nullable=False, default=True,
                                    server_default=sa.true())
    allow_input = db.Column(db.Boolean, nullable=False, default=False,
                            server_default=sa.false())
    # pbkdf2-sha256$<iter>$<salt hex>$<hash hex>; guards leaving the native apps.
    unlock_pin_hash = db.Column(db.String(200), nullable=True)
    # Lock player: the native apps cannot be left without the unlock PIN.
    kiosk_lock = db.Column(db.Boolean, nullable=False, default=False,
                           server_default=sa.false())
    resume_after_s = db.Column(db.Integer, nullable=False, default=RESUME_DEFAULT_S,
                               server_default="300")
    # Device agent (docs/device-agent-design.md). Admin-only, all off by default: the watchdog
    # restarts the player when it hangs; the allow_* switches let an admin issue that command.
    agent_watchdog = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    agent_allow_restart = db.Column(db.Boolean, nullable=False, default=False,
                                    server_default=sa.false())
    agent_allow_logs = db.Column(db.Boolean, nullable=False, default=False,
                                 server_default=sa.false())
    # Tier B (signed) commands; each is separate because each is more dangerous (docs/signed-commands.md).
    agent_allow_reboot = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    agent_allow_startup = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    agent_allow_update = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())
    # "How the player runs" chosen when a PC was set up from the admin (app | autostart | shell): applied as a signed
    # set_startup command the first time the privileged agent reports it can (then cleared).
    pending_run_as = db.Column(db.String(12), nullable=True)

    # Privileged (SYSTEM) Windows agent (docs/windows-agent-service.md). It has its own credential, separate
    # from the display token. Only sha256 of the token and of the one-time enrollment code is stored.
    # Written with Core UPDATE only (an ORM write would broadcast a manifest hint to the screen).
    agent_token_hash = db.Column(db.String(64), nullable=True, unique=True, index=True)
    agent_enroll_hash = db.Column(db.String(64), nullable=True, index=True)
    agent_enroll_expires = db.Column(db.DateTime(timezone=True), nullable=True)
    agent_enroll_fails = db.Column(db.Integer, nullable=False, default=0, server_default="0")
    agent_pubkey = db.Column(db.String(64), nullable=True)   # base64 X25519 public key (auto-login envelopes)
    agent_diag = db.Column(db.Text, nullable=True)           # cleaned JSON the agent reported
    agent_seen_at = db.Column(db.DateTime(timezone=True), nullable=True)
    agent_enrolled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # The admin ticked "also enable the privileged agent" when pairing: the next agent request from this PC is approved by itself.
    agent_auto_approve = db.Column(db.Boolean, nullable=False, default=False, server_default=sa.false())

    # Device-reported data and screenshot requests. These are written only with Core
    # UPDATE; an ORM write broadcasts a manifest hint to every screen.
    diagnostics = db.Column(db.Text, nullable=True)  # JSON, at most 4096 characters
    diagnostics_at = db.Column(db.DateTime(timezone=True), nullable=True)
    screenshot_request = db.Column(db.String(32), nullable=True)
    screenshot_requested_at = db.Column(db.DateTime(timezone=True), nullable=True)
    screenshot_at = db.Column(db.DateTime(timezone=True), nullable=True)
    # Alerts: a muted display never raises "display offline". Written with Core UPDATE.
    alerts_muted = db.Column(db.Boolean, nullable=False, default=False,
                             server_default=sa.false())

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
