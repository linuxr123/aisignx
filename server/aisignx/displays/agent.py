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
                                     ACTION_SET_AUTOLOGIN, ACTION_SET_STARTUP, ACTION_UPDATE_APP, ACTIONS, OPEN_STATES,
                                     SIGNED_ACTIONS, STATE_DELIVERED, STATE_DONE, STATE_EXPIRED,
                                     STATE_FAILED, STATE_PENDING)
from ..timeutil import as_utc, utcnow

# How long a command may wait for its device (server time).
TTL_S = {ACTION_RESTART_PLAYER: 120, ACTION_COLLECT_LOGS: 300, ACTION_REBOOT: 120,
         ACTION_SET_STARTUP: 300, ACTION_UPDATE_APP: 900, ACTION_SET_AUTOLOGIN: 300}
REBOOT_GAP_MIN = 10        # a reboot loop (or a mistake) cannot keep a screen down
MAX_PER_HOUR = 10          # commands per display: a loop or a mistake cannot hammer a device
KEEP_DAYS = 30             # finished commands are deleted after this long
MAX_RESULT = 200
GZIP_MAGIC = bytes([0x1F, 0x8B])  # the first two bytes of every gzip file
MAX_LOG_BYTES = 1_000_000  # an uploaded log (gzip): about a day of a busy player's log, compressed
LOGS_PER_DISPLAY = 5       # the newest few are kept
LOG_KEEP_DAYS = 7
_REQUEST_ID = re.compile(r"^[0-9a-f]{16}$")
_USER = re.compile(r"[A-Za-z0-9._ -]{1,64}")
_KEY_ID = re.compile(r"[0-9a-f]{16}")
_B64 = re.compile(r"[A-Za-z0-9+/]+={0,2}")

LABELS = {ACTION_RESTART_PLAYER: "Restart the player", ACTION_COLLECT_LOGS: "Collect logs",
          ACTION_REBOOT: "Reboot the device", ACTION_SET_STARTUP: "Change the startup settings",
          ACTION_UPDATE_APP: "Update the app", ACTION_SET_AUTOLOGIN: "Change the automatic sign-in"}
# display column that must be on, device capability key that must be true.
_SETTING = {ACTION_RESTART_PLAYER: "agent_allow_restart", ACTION_COLLECT_LOGS: "agent_allow_logs",
            ACTION_REBOOT: "agent_allow_reboot", ACTION_SET_STARTUP: "agent_allow_startup",
            ACTION_UPDATE_APP: "agent_allow_update"}   # set_autologin has none: it needs the privileged agent
_CAP = {ACTION_RESTART_PLAYER: "can_restart", ACTION_COLLECT_LOGS: "can_collect_logs",
        ACTION_REBOOT: "can_reboot", ACTION_SET_STARTUP: "can_set_startup", ACTION_UPDATE_APP: "can_update",
        ACTION_SET_AUTOLOGIN: "can_autologin"}
MODES = ("app", "shell")
# "How the player runs": one choice, the same on Windows and Android (display page). Each sets both settings.
RUN_MODES = {"app": {"autostart": False, "mode": "app"},
             "autostart": {"autostart": True, "mode": "app"},
             "shell": {"autostart": True, "mode": "shell"}}


# What only the privileged agent can vouch for (an unprivileged player's claims are not evidence).
PRIVILEGED_CAPS = ("can_reboot", "can_update", "can_set_startup", "can_autologin")


def capabilities(display: Display) -> dict:
    """What the device reported in its diagnostics ("agent" block); {} if it reports none. Once the
    privileged agent is enrolled it alone speaks for the privileged abilities."""
    try:
        data = json.loads(display.diagnostics or "{}")
    except (TypeError, ValueError):
        return {}
    agent = data.get("agent") if isinstance(data, dict) else None
    caps = dict(agent) if isinstance(agent, dict) else {}
    if display.agent_token_hash:
        from . import enroll
        diag = enroll.diag_of(display)
        for key in PRIVILEGED_CAPS:
            caps[key] = diag.get(key) is True
        caps["startup"] = diag.get("startup") if isinstance(diag.get("startup"), dict) else {}
        caps["tier"] = "system"
        caps["privileged_version"] = diag.get("version")
    return caps


def hidden_from_player(display: Display) -> tuple:
    """Actions the display-token routes never deliver: set_autologin always, all Tier B once an agent is enrolled."""
    return SIGNED_ACTIONS if display.agent_token_hash else (ACTION_SET_AUTOLOGIN,)


def _why_not(display: Display, action: str) -> str | None:
    """A sentence saying why `action` cannot be issued to `display` right now, or None."""
    if action not in ACTIONS:
        return "Unknown command."
    if not display.is_paired:
        return "This display is not paired."
    if action == ACTION_SET_AUTOLOGIN:
        if not display.agent_token_hash:
            return "Automatic sign-in needs the privileged Windows agent (Device agent > Enroll)."
    elif not getattr(display, _SETTING[action]):
        return f"{LABELS[action]} is switched off for this display (Edit > Device agent)."
    if display.agent_token_hash and action in SIGNED_ACTIONS:
        from . import enroll
        if enroll.summary(display)["stale"]:
            return "The privileged agent has not checked in recently."
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
    if action == ACTION_SET_AUTOLOGIN:
        if params.get("enabled") is False and set(params) == {"enabled"}:
            return {"enabled": False}, None
        ok = (params.get("enabled") is True and set(params) == {"enabled", "user", "domain", "key_id", "enc"}
              and all(isinstance(params[k], str) for k in ("user", "domain", "key_id", "enc"))
              and _USER.fullmatch(params["user"]) and params["domain"] == "." and _KEY_ID.fullmatch(params["key_id"])
              and len(params["enc"]) <= 600 and _B64.fullmatch(params["enc"]))
        return (dict(params), None) if ok else (None, "Bad automatic sign-in request.")
    return {}, None  # reboot_device and the Tier A actions take no parameters


def update_package(display: Display):
    """The Download this display would be updated with: the newest installer of its app's kind (Android: signed with
    the key the app reported, when the page has one; see downloads.service.installer_for), or None."""
    from ..downloads.service import SHELL_KINDS, display_signer, installer_for, latest_download

    if display.agent_token_hash:   # the privileged agent is Windows; the unprivileged player does not choose
        return latest_download(SHELL_KINDS["electron"])
    try:
        shell = json.loads(display.diagnostics or "{}").get("shell") or {}
    except (TypeError, ValueError, AttributeError):
        shell = {}
    kind = SHELL_KINDS.get(str(shell.get("name") or ""))
    return installer_for(kind, display_signer(display)) if kind else None


def signer_problem(display: Display, package) -> str | None:
    """Android refuses an update signed with another key than the installed app's. When the app has reported its key (the agent block's
    "signer") and the installer's key can be read, a different pair is refused here, in words, instead of failing on the device."""
    from ..downloads import apksign
    from ..downloads.routes import downloads_dir

    caps = capabilities(display)
    own = str(caps.get("signer") or "")
    if not own or package is None or not package.filename.lower().endswith(".apk"):
        return None
    theirs = apksign.signer_sha256(downloads_dir() / package.stored_name)
    if theirs and theirs.lower() != own.lower():
        return ("This screen's app was installed with a different signing key than the installer on the Downloads page, and Android refuses "
                "such an update. Reinstall the app once from the Downloads page (a Device Owner tablet must be factory reset first), "
                "or upload an installer signed with the key this app uses.")
    return None


def create(display: Display, action: str, user, now: datetime | None = None, params: dict | None = None,
           request_id: str | None = None) -> tuple[DeviceCommand | None, str | None]:
    """(command, None) or (None, reason). One open command per display and action."""
    now = now or utcnow()
    reason = _why_not(display, action)
    if reason:
        return None, reason
    if action == ACTION_UPDATE_APP:
        package = update_package(display)
        if package is None:
            return None, "There is no installer for this screen's app on the Downloads page."
        problem = signer_problem(display, package)
        if problem:
            return None, problem
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
    cmd = DeviceCommand(display_id=display.id, request_id=request_id or secrets.token_hex(8), action=action, seq=seq,
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
        if cmd.action == ACTION_SET_AUTOLOGIN:
            cmd.params = None   # an envelope nobody fetched is not kept
        n += 1
    return n


def pending_count(display_id: int, now: datetime | None = None, exclude: tuple = ()) -> int:
    now = now or utcnow()
    return DeviceCommand.query.filter(DeviceCommand.display_id == display_id,
                                      DeviceCommand.state == STATE_PENDING,
                                      DeviceCommand.action.notin_(exclude),
                                      DeviceCommand.expires_at > now).count()


def deliver(display: Display, now: datetime | None = None, only: tuple | None = None,
            exclude: tuple = ()) -> list[DeviceCommand]:
    """The display's commands that are waiting (and still valid), marked delivered. A command
    already delivered is not handed out again: it runs at most once."""
    now = now or utcnow()
    expire_due(now)
    query = DeviceCommand.query.filter(DeviceCommand.display_id == display.id,
                                       DeviceCommand.state == STATE_PENDING,
                                       DeviceCommand.action.notin_(exclude))
    if only is not None:
        query = query.filter(DeviceCommand.action.in_(only))
    cmds = query.order_by(DeviceCommand.id).limit(5).all()
    for cmd in cmds:
        cmd.state = STATE_DELIVERED
        cmd.delivered_at = now
    return cmds


def acknowledge(display: Display, request_id: str, state: str, result: str | None,
                now: datetime | None = None, only: tuple | None = None, exclude: tuple = ()) -> tuple[DeviceCommand | None, str | None]:
    """(command, None) or (None, error). Only this display's own command, only done or failed,
    and acknowledging twice with the same answer is fine."""
    now = now or utcnow()
    if state not in (STATE_DONE, STATE_FAILED):
        return None, "state must be done or failed"
    cmd = DeviceCommand.query.filter_by(display_id=display.id, request_id=str(request_id)[:32]).first()
    if cmd is None or cmd.action in exclude or (only is not None and cmd.action not in only):
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
    for cmd in DeviceCommand.query.filter(DeviceCommand.action == ACTION_SET_AUTOLOGIN,
                                          DeviceCommand.state.notin_(OPEN_STATES)).all():
        if isinstance(cmd.params, dict) and "enc" in cmd.params:
            cmd.params = None
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


def scrub_delivered(cmd: DeviceCommand) -> None:
    """Called right after a command was signed for delivery: an auto-login envelope is removed from the row, so
    the database and its backups hold the ciphertext only until the agent has fetched it."""
    if cmd.action == ACTION_SET_AUTOLOGIN and isinstance(cmd.params, dict) and cmd.params.get("enabled"):
        cmd.params = {"enabled": True, "user": cmd.params.get("user"), "delivered": True}


def apply_pending_run_mode(display: Display, now: datetime | None = None) -> DeviceCommand | None:
    """A PC set up from the admin ("Set up a Windows PC") chose how the player runs before the agent existed. The first
    time the privileged agent can do it, send that choice as the usual signed set_startup command, then forget it.
    Returns the command, or None (nothing pending, or not possible yet: tried again at the agent's next report)."""
    run_as = display.pending_run_as
    if run_as not in RUN_MODES:
        if run_as:
            display.pending_run_as = None
        return None
    cmd, _reason = create(display, ACTION_SET_STARTUP, None, now, params=dict(RUN_MODES[run_as]))
    if cmd is None:
        return None
    display.pending_run_as = None
    return cmd
