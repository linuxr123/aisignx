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

from sqlalchemy import func

from ..extensions import db
from ..media import storage
from ..models import DeviceCommand, Display
from ..models.device_command import (ACTION_COLLECT_LOGS, ACTION_REBOOT, ACTION_RESTART_PLAYER,
                                     ACTION_SET_STARTUP, ACTION_UPDATE_APP, ACTIONS, OPEN_STATES,
                                     SIGNED_ACTIONS, STATE_DELIVERED, STATE_DONE, STATE_EXPIRED,
                                     STATE_FAILED, STATE_PENDING)
from ..timeutil import as_utc, utcnow

# How long a command may wait for its device (server time).
TTL_S = {ACTION_RESTART_PLAYER: 120, ACTION_COLLECT_LOGS: 300, ACTION_REBOOT: 120,
         ACTION_SET_STARTUP: 300, ACTION_UPDATE_APP: 900}
REBOOT_GAP_MIN = 10        # a reboot loop (or a mistake) cannot keep a screen down
MAX_PER_HOUR = 10          # commands per display: a loop or a mistake cannot hammer a device
KEEP_DAYS = 30             # finished commands are deleted after this long
MAX_RESULT = 200
GZIP_MAGIC = bytes([0x1F, 0x8B])  # the first two bytes of every gzip file
MAX_LOG_BYTES = 1_000_000  # an uploaded log (gzip): about a day of a busy player's log, compressed
LOGS_PER_DISPLAY = 5       # the newest few are kept
LOG_KEEP_DAYS = 7
_REQUEST_ID = re.compile(r"^[0-9a-f]{16}$")

LABELS = {ACTION_RESTART_PLAYER: "Restart the player", ACTION_COLLECT_LOGS: "Collect logs",
          ACTION_REBOOT: "Reboot the device", ACTION_SET_STARTUP: "Change the startup settings",
          ACTION_UPDATE_APP: "Update the app"}
# display column that must be on, device capability key that must be true.
_SETTING = {ACTION_RESTART_PLAYER: "agent_allow_restart", ACTION_COLLECT_LOGS: "agent_allow_logs",
            ACTION_REBOOT: "agent_allow_reboot", ACTION_SET_STARTUP: "agent_allow_startup",
            ACTION_UPDATE_APP: "agent_allow_update"}
_CAP = {ACTION_RESTART_PLAYER: "can_restart", ACTION_COLLECT_LOGS: "can_collect_logs",
        ACTION_REBOOT: "can_reboot", ACTION_SET_STARTUP: "can_set_startup", ACTION_UPDATE_APP: "can_update"}
MODES = ("app", "shell")


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


def validate_params(display: Display, action: str, params) -> tuple[dict | None, str | None]:
    """(clean params, None) or (None, reason). Typed and closed: nothing but what is listed is accepted."""
    params = params if isinstance(params, dict) else {}
    if action == ACTION_SET_STARTUP:
        clean = {}
        if "autostart" in params:
            if not isinstance(params["autostart"], bool):
                return None, "autostart must be on or off."
            clean["autostart"] = params["autostart"]
        if "mode" in params:
            if params["mode"] not in MODES:
                return None, "mode must be app or shell."
            if params["mode"] == "shell" and not (capabilities(display).get("startup") or {}).get("shell_capable"):
                return None, "This screen cannot run as the shell (Windows only, and it must be able to undo it)."
            clean["mode"] = params["mode"]
        if not clean:
            return None, "Choose what to change."
        return clean, None
    if action == ACTION_UPDATE_APP:
        version, sha, size = str(params.get("version") or ""), str(params.get("sha256") or ""), params.get("size")
        if not version or not re.fullmatch(r"[0-9a-f]{64}", sha) or not isinstance(size, int) or size <= 0:
            return None, "No installer to push."
        return {"version": version[:32], "sha256": sha, "size": size}, None
    return {}, None  # reboot_device and the Tier A actions take no parameters


def update_package(display: Display):
    """The Download this display would be updated with: the newest installer of its app's kind, or None."""
    from ..downloads.service import SHELL_KINDS, latest_download

    try:
        shell = json.loads(display.diagnostics or "{}").get("shell") or {}
    except (TypeError, ValueError, AttributeError):
        shell = {}
    kind = SHELL_KINDS.get(str(shell.get("name") or ""))
    return latest_download(kind) if kind else None


def create(display: Display, action: str, user, now: datetime | None = None, params: dict | None = None
           ) -> tuple[DeviceCommand | None, str | None]:
    """(command, None) or (None, reason). One open command per display and action."""
    now = now or utcnow()
    reason = _why_not(display, action)
    if reason:
        return None, reason
    if action == ACTION_UPDATE_APP:
        package = update_package(display)
        if package is None:
            return None, "There is no installer for this screen's app on the Downloads page."
        params = {"version": package.version, "sha256": package.sha256, "size": package.size}
    clean, reason = validate_params(display, action, params)
    if reason:
        return None, reason
    if action == ACTION_REBOOT:
        gap = now - timedelta(minutes=REBOOT_GAP_MIN)
        if DeviceCommand.query.filter(DeviceCommand.display_id == display.id, DeviceCommand.action == ACTION_REBOOT,
                                      DeviceCommand.created_at >= gap).count():
            return None, f"This screen was asked to reboot in the last {REBOOT_GAP_MIN} minutes."
    if open_command(display.id, action, now) is not None:
        return None, f"{LABELS[action]} is already waiting for this screen."
    recent = DeviceCommand.query.filter(DeviceCommand.display_id == display.id,
                                        DeviceCommand.created_at >= now - timedelta(hours=1)).count()
    if recent >= MAX_PER_HOUR:
        return None, "Too many commands for this screen in the last hour. Try again later."
    last = db.session.query(func.max(DeviceCommand.seq)).filter(DeviceCommand.display_id == display.id).scalar() or 0
    seq = max(int(now.timestamp() * 1000), int(last) + 1)
    cmd = DeviceCommand(display_id=display.id, request_id=secrets.token_hex(8), action=action, seq=seq,
                        params=clean or None, state=STATE_PENDING, created_by_id=getattr(user, "id", None),
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


# --- the installer for an open update_app command ---------------------------------------------------
# The device may fetch the package only while its own update_app command is open (delivered, not expired).
# The server picks the file (the Download whose size and sha256 are in the signed command); the device
# checks both again before installing.

def package_for(display: Display, request_id: str, now: datetime | None = None):
    """(download row, path) for this display's open update_app command, else (None, reason)."""
    from ..downloads.routes import downloads_dir
    from ..models import Download

    now = now or utcnow()
    cmd = DeviceCommand.query.filter_by(display_id=display.id, request_id=str(request_id)[:32],
                                        action=ACTION_UPDATE_APP).first()
    if cmd is None:
        return None, "unknown command"
    if cmd.state != STATE_DELIVERED or as_utc(cmd.expires_at) <= now:
        return None, "not open"
    want = cmd.params or {}
    row = Download.query.filter_by(sha256=str(want.get("sha256") or ""), size=int(want.get("size") or 0)).first()
    path = downloads_dir() / row.stored_name if row else None
    if row is None or not path.is_file():
        return None, "the installer is gone"
    return row, path
