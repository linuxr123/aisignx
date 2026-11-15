"""Device agent commands: create, deliver, acknowledge, expire (docs/device-agent-design.md).

An admin asks for a command; the row is stored (the truth); the display is told to look (SSE
`{"action": "commands"}` and the heartbeat's `commands` count); the display fetches its own
commands, runs each at most once and acknowledges it. A command can only be created when the
admin switched that ability on for the display AND the device says it can do it, and a display
can only ever see and acknowledge its own commands.

Nothing here commits; callers do. Display rows are never ORM-written here: that would
broadcast a manifest hint to every screen.
"""
from __future__ import annotations

import json
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

from ..extensions import db
from ..media import storage
from ..models import DeviceCommand, Display
from ..models.device_command import (ACTION_COLLECT_LOGS, ACTION_RESTART_PLAYER, ACTIONS,
                                     OPEN_STATES, STATE_DELIVERED, STATE_DONE, STATE_EXPIRED,
                                     STATE_FAILED, STATE_PENDING)
from ..timeutil import as_utc, utcnow

# How long a command may wait for its device (server time).
TTL_S = {ACTION_RESTART_PLAYER: 120, ACTION_COLLECT_LOGS: 300}
MAX_PER_HOUR = 10          # commands per display: a loop or a mistake cannot hammer a device
KEEP_DAYS = 30             # finished commands are deleted after this long
MAX_RESULT = 200
GZIP_MAGIC = bytes([0x1F, 0x8B])  # the first two bytes of every gzip file
MAX_LOG_BYTES = 1_000_000  # an uploaded log (gzip): about a day of a busy player's log, compressed
LOGS_PER_DISPLAY = 5       # the newest few are kept
LOG_KEEP_DAYS = 7
_REQUEST_ID = re.compile(r"^[0-9a-f]{16}$")

LABELS = {ACTION_RESTART_PLAYER: "Restart the player", ACTION_COLLECT_LOGS: "Collect logs"}
# display column that must be on, device capability key that must be true.
_SETTING = {ACTION_RESTART_PLAYER: "agent_allow_restart", ACTION_COLLECT_LOGS: "agent_allow_logs"}
_CAP = {ACTION_RESTART_PLAYER: "can_restart", ACTION_COLLECT_LOGS: "can_collect_logs"}


def capabilities(display: Display) -> dict:
    """What the device reported in its diagnostics ("agent" block); {} if it reports none."""
    try:
        data = json.loads(display.diagnostics or "{}")
    except (TypeError, ValueError):
        return {}
    agent = data.get("agent") if isinstance(data, dict) else None
    return agent if isinstance(agent, dict) else {}


def _why_not(display: Display, action: str) -> str | None:
    """A sentence saying why `action` cannot be issued to `display` right now, or None."""
    if action not in ACTIONS:
        return "Unknown command."
    if not display.is_paired:
        return "This display is not paired."
    if not getattr(display, _SETTING[action]):
        return f"{LABELS[action]} is switched off for this display (Edit > Device agent)."
    caps = capabilities(display)
    if not caps:
        return "This screen has not reported an agent yet. Update its app and wait for it to check in."
    if not caps.get(_CAP[action]):
        return f"This screen's app cannot do that ({LABELS[action].lower()})."
    return None


def open_command(display_id: int, action: str, now: datetime | None = None) -> DeviceCommand | None:
    now = now or utcnow()
    expire_due(now)
    return (DeviceCommand.query.filter(DeviceCommand.display_id == display_id,
                                       DeviceCommand.action == action,
                                       DeviceCommand.state.in_(OPEN_STATES))
            .order_by(DeviceCommand.id.desc()).first())


def create(display: Display, action: str, user, now: datetime | None = None
           ) -> tuple[DeviceCommand | None, str | None]:
    """(command, None) or (None, reason). One open command per display and action."""
    now = now or utcnow()
    reason = _why_not(display, action)
    if reason:
        return None, reason
    if open_command(display.id, action, now) is not None:
        return None, f"{LABELS[action]} is already waiting for this screen."
    recent = DeviceCommand.query.filter(DeviceCommand.display_id == display.id,
                                        DeviceCommand.created_at >= now - timedelta(hours=1)).count()
    if recent >= MAX_PER_HOUR:
        return None, "Too many commands for this screen in the last hour. Try again later."
    cmd = DeviceCommand(display_id=display.id, request_id=secrets.token_hex(8), action=action,
                        params=None, state=STATE_PENDING, created_by_id=getattr(user, "id", None),
                        created_at=now, expires_at=now + timedelta(seconds=TTL_S[action]))
    db.session.add(cmd)
    db.session.flush()
    return cmd, None


def expire_due(now: datetime | None = None) -> int:
    """Mark open commands whose time is up as expired. Returns how many."""
    now = now or utcnow()
    n = 0
    for cmd in DeviceCommand.query.filter(DeviceCommand.state.in_(OPEN_STATES),
                                          DeviceCommand.expires_at <= now).all():
        cmd.state = STATE_EXPIRED
        cmd.result = "Not carried out in time."
        n += 1
    return n


def pending_count(display_id: int, now: datetime | None = None) -> int:
    now = now or utcnow()
    return DeviceCommand.query.filter(DeviceCommand.display_id == display_id,
                                      DeviceCommand.state == STATE_PENDING,
                                      DeviceCommand.expires_at > now).count()


def deliver(display: Display, now: datetime | None = None) -> list[DeviceCommand]:
    """The display's commands that are waiting (and still valid), marked delivered. A command
    already delivered is not handed out again: it runs at most once."""
    now = now or utcnow()
    expire_due(now)
    cmds = (DeviceCommand.query.filter(DeviceCommand.display_id == display.id,
                                       DeviceCommand.state == STATE_PENDING)
            .order_by(DeviceCommand.id).limit(5).all())
    for cmd in cmds:
        cmd.state = STATE_DELIVERED
        cmd.delivered_at = now
    return cmds


def acknowledge(display: Display, request_id: str, state: str, result: str | None,
                now: datetime | None = None) -> tuple[DeviceCommand | None, str | None]:
    """(command, None) or (None, error). Only this display's own command, only done or failed,
    and acknowledging twice with the same answer is fine."""
    now = now or utcnow()
    if state not in (STATE_DONE, STATE_FAILED):
        return None, "state must be done or failed"
    cmd = DeviceCommand.query.filter_by(display_id=display.id, request_id=str(request_id)[:32]).first()
    if cmd is None:
        return None, "unknown command"
    if cmd.state in (STATE_DONE, STATE_FAILED):
        return (cmd, None) if cmd.state == state else (None, "already acknowledged")
    if cmd.state == STATE_EXPIRED or as_utc(cmd.expires_at) <= now:
        cmd.state = STATE_EXPIRED
        return None, "expired"
    if cmd.state not in (STATE_DELIVERED, STATE_PENDING):
        return None, "not open"
    cmd.state = state
    cmd.acked_at = now
    cmd.result = (result or "")[:MAX_RESULT] or None
    return cmd, None


# --- uploaded logs ----------------------------------------------------------------------------
# A device can only upload a log for a collect_logs command that is open and was delivered to it; the
# server picks the path, never the device; the file is stored as it came (gzip) and never decompressed
# here; an admin downloads it.

def logs_dir(display_id: int) -> Path:
    path = storage.work_dir().parent / "agent-logs" / str(int(display_id))
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_path(display_id: int, request_id: str) -> Path | None:
    if not _REQUEST_ID.match(str(request_id)):
        return None
    return logs_dir(display_id) / f"{request_id}.log.gz"


def save_logs(display: Display, request_id: str, data: bytes, now: datetime | None = None
              ) -> tuple[DeviceCommand | None, str | None]:
    """Store an uploaded log for an open collect_logs command. (command, None) or (None, error)."""
    now = now or utcnow()
    if not data or len(data) > MAX_LOG_BYTES:
        return None, "too large" if data else "empty"
    if data[:2] != GZIP_MAGIC:
        return None, "not gzip"
    cmd = DeviceCommand.query.filter_by(display_id=display.id, request_id=str(request_id)[:32],
                                        action=ACTION_COLLECT_LOGS).first()
    if cmd is None:
        return None, "unknown command"
    if cmd.state != STATE_DELIVERED or as_utc(cmd.expires_at) <= now:
        return None, "not open"
    path = log_path(display.id, cmd.request_id)
    path.write_bytes(data)
    cmd.state = STATE_DONE
    cmd.acked_at = now
    cmd.result = f"uploaded {max(1, len(data) // 1024)} KB"
    _prune(display.id)
    return cmd, None


def _prune(display_id: int) -> None:
    files = sorted(logs_dir(display_id).glob("*.log.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[LOGS_PER_DISPLAY:]:
        old.unlink(missing_ok=True)


def prune_all(now: float | None = None) -> int:
    """Delete logs older than LOG_KEEP_DAYS. Returns how many."""
    cutoff = (now or time.time()) - LOG_KEEP_DAYS * 86400
    base = storage.work_dir().parent / "agent-logs"
    n = 0
    if base.is_dir():
        for path in base.glob("*/*.log.gz"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                n += 1
    return n


def job() -> str | None:
    """Job: expire commands that were never carried out, and delete finished ones after KEEP_DAYS."""
    now = utcnow()
    expired = expire_due(now)
    old = DeviceCommand.query.filter(DeviceCommand.state.notin_(OPEN_STATES),
                                     DeviceCommand.created_at < now - timedelta(days=KEEP_DAYS))
    removed = old.delete(synchronize_session=False)
    db.session.commit()
    pruned = prune_all()
    return (f"expired {expired}, removed {removed}, logs pruned {pruned}"
            if expired or removed or pruned else None)
