"""The App updates page: which screens run an older app than the installer on the Downloads page, a way to push the update to just the
screens you tick, and a live status for each one (waiting, installing, done, failed). The push itself is the usual signed `update_app`
command (displays/agent.py), so every rule that applies to one screen applies here: the switch on that screen, what the app can do, one open
command at a time."""
from __future__ import annotations

from ..downloads.service import KIND_LABELS, SHELL_KINDS, _parse, latest_download
from ..models import DeviceCommand, Display
from ..models.device_command import ACTION_UPDATE_APP, OPEN_STATES, STATE_DELIVERED, STATE_DONE, STATE_EXPIRED, STATE_FAILED, STATE_PENDING
from . import agent, options, platform

STATUS_TEXT = {
    "current": "Up to date",
    "outdated": "Update available",
    "ahead": "Newer than the Downloads page",
    "queued": "Waiting for the screen to pick it up",
    "installing": "Installing",
    "done": "Installed",
    "failed": "Failed",
    "expired": "Not picked up in time",
    "unknown": "Version not reported",
}


def _shell(display: Display) -> dict:
    diag = options.parse_diag(display.diagnostics)
    shell = diag.get("shell") if isinstance(diag, dict) else None
    return shell if isinstance(shell, dict) else {}


def rows(displays) -> list[dict]:
    """One row per paired screen that runs an AiSignX app, newest status first within the name order."""
    out = []
    last = {}
    ids = [d.id for d in displays]
    if ids:
        for c in DeviceCommand.query.filter(DeviceCommand.display_id.in_(ids), DeviceCommand.action == ACTION_UPDATE_APP).order_by(DeviceCommand.id).all():
            last[c.display_id] = c                                     # the highest id wins
    published = {kind: latest_download(kind) for kind in set(SHELL_KINDS.values())}
    for d in displays:
        shell = _shell(d)
        kind = SHELL_KINDS.get(str(shell.get("name") or ""))
        if not kind or not d.is_paired:
            continue
        running = _parse(shell.get("version"))
        pub = published.get(kind)
        pub_v = _parse(pub.version) if pub else None
        cmd = last.get(d.id)
        if running is None or pub_v is None:
            status = "unknown" if running is None else "current"
        elif running < pub_v:
            status = "outdated"
        elif running > pub_v:
            status = "ahead"
        else:
            status = "current"
        # An update that is open or just failed says more than "outdated".
        if cmd is not None and cmd.state in OPEN_STATES:
            status = "queued" if cmd.state == STATE_PENDING else "installing"
        elif cmd is not None and status == "outdated" and cmd.state in (STATE_FAILED, STATE_EXPIRED):
            status = "failed" if cmd.state == STATE_FAILED else "expired"
        elif cmd is not None and cmd.state == STATE_DONE and status == "current":
            status = "done"
        caps = agent.capabilities(d)
        reason = None
        if status in ("outdated", "failed", "expired"):
            reason = agent._why_not(d, ACTION_UPDATE_APP)              # why a push would be refused right now (None: allowed)
            if reason is None and not caps.get("can_update"):
                reason = "This screen's app cannot update itself."
            if reason is None:
                reason = agent.signer_problem(d, pub)
        out.append({"display": d, "system": platform.words(platform.of_display(d))["label"], "kind": kind, "app": KIND_LABELS.get(kind, kind),
                    "running": str(shell.get("version") or "?"), "latest": pub.version if pub else None, "status": status,
                    "text": STATUS_TEXT[status], "result": (cmd.result if cmd is not None and cmd.state in (STATE_FAILED, STATE_EXPIRED, STATE_DONE) else None),
                    "when": cmd.created_at if cmd is not None else None, "can_push": reason is None and status in ("outdated", "failed", "expired"),
                    "reason": reason})
    return out


def push(displays, user) -> dict:
    """Create the update command for each display. -> {"sent": [names], "skipped": [(name, reason)]}. The caller commits and wakes the screens."""
    sent, skipped, commands = [], [], []
    for d in displays:
        cmd, reason = agent.create(d, ACTION_UPDATE_APP, user)
        if cmd is None:
            skipped.append((d.name, reason))
        else:
            sent.append(d.name)
            commands.append((d, cmd))
    return {"sent": sent, "skipped": skipped, "commands": commands}
