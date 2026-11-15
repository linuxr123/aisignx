"""Write audit rows. Call record() before the caller's commit, so the row commits (or
rolls back) together with the change it describes.

    audit.record("playlist.update", playlist, detail={"changed": ["name"]})
    db.session.commit()

Who, how and from where are filled in automatically: an API token's user (g.api_user),
else the signed-in user, else `user=`; via is "api", "web", "cli" or "system".
"""
import json
import logging
import re
from collections import OrderedDict
from datetime import timedelta

import click
import sqlalchemy as sa
from flask import g, has_request_context, request
from flask_login import current_user

from ..extensions import db
from ..models import AuditLog, Setting
from ..timeutil import utcnow

log = logging.getLogger(__name__)

MAX_STRING = 500
MAX_DETAIL = 4096
_MAX_DEPTH = 8
_SECRET_KEY = re.compile(r"password|token|secret|pin|key", re.IGNORECASE)
REDACTED = "***"

# Filter choices for the audit viewer. A key ending in "." is a family and matches every
# action that starts with it; any other key matches that one action exactly.
ACTIONS = OrderedDict([
    ("auth.", "Sign-in (all)"),
    ("auth.login", "Signed in"),
    ("auth.login_failed", "Sign-in failed"),
    ("auth.logout", "Signed out"),
    ("auth.password_change", "Changed own password"),
    ("auth.rate_limited", "Sign-in rate limited"),
    ("setup.admin_created", "First admin created (setup)"),
    ("user.", "Users (all)"),
    ("user.create", "User created"),
    ("user.update", "User updated"),
    ("user.delete", "User deleted"),
    ("user.password_reset", "User password reset"),
    ("media.", "Media (all)"),
    ("media.upload", "Media uploaded"),
    ("media.create_web", "Web page added"),
    ("media.create_plugin", "Plugin item added"),
    ("media.update", "Media updated"),
    ("media.replace", "Media file replaced"),
    ("media.convert", "Video converted (TV-friendly copy)"),
    ("media.delete", "Media deleted"),
    ("playlist.", "Playlists (all)"),
    ("playlist.create", "Playlist created"),
    ("playlist.update", "Playlist updated"),
    ("playlist.delete", "Playlist deleted"),
    ("playlist.item_add", "Playlist item added"),
    ("playlist.item_update", "Playlist item updated"),
    ("playlist.item_remove", "Playlist item removed"),
    ("playlist.reorder", "Playlist reordered"),
    ("display.", "Displays (all)"),
    ("display.pair", "Display paired"),
    ("display.update", "Display updated"),
    ("display.unpair", "Display unpaired"),
    ("display.delete", "Display deleted"),
    ("display.screenshot_request", "Screenshot requested"),
    ("display.reload", "Display reloaded"),
    ("display.agent", "Device agent command"),
    ("display.agent_command", "Device agent command issued"),
    ("display.agent_result", "Device agent command finished"),
    ("display.identify", "Display identified"),
    ("display.unlock", "Player unlocked remotely"),
    ("display.relock", "Player locked again"),
    ("group.", "Groups (all)"),
    ("group.create", "Group created"),
    ("group.update", "Group updated"),
    ("group.delete", "Group deleted"),
    ("schedule.", "Schedules (all)"),
    ("schedule.create", "Schedule created"),
    ("schedule.update", "Schedule updated"),
    ("schedule.delete", "Schedule deleted"),
    ("schedule.toggle", "Schedule turned on/off"),
    ("emergency.", "Emergency (all)"),
    ("emergency.send", "Emergency sent"),
    ("emergency.clear", "Emergency cleared"),
    ("emergency.clear_all", "All emergencies cleared"),
    ("settings.update", "Settings changed"),
    ("plugin.", "Plugins (all)"),
    ("plugin.settings", "Plugin settings changed"),
    ("plugin.cache_clear", "Plugin cache cleared"),
    ("token.", "API tokens (all)"),
    ("token.create", "API token created"),
    ("token.revoke", "API token revoked"),
    ("backup.", "Backups (all)"),
    ("backup.create", "Backup created"),
    ("backup.download", "Backup downloaded"),
    ("backup.upload", "Backup uploaded"),
    ("backup.restore", "Backup restored"),
    ("backup.delete", "Backup deleted"),
    ("backup.settings", "Backup settings changed"),
    ("alert.", "Alerts (all)"),
    ("alert.ack", "Alert acknowledged"),
    ("alert.mute", "Display alerts muted"),
    ("alert.unmute", "Display alerts unmuted"),
    ("alerts.settings", "Alert settings changed"),
    ("smtp.test", "Test e-mail sent"),
    ("pop.settings", "Proof-of-play settings changed"),
    ("audit.settings", "Audit settings changed"),
])


def action_label(action: str) -> str:
    return ACTIONS.get(action, action)


def _redact(value, depth: int = 0):
    """Copy of value with secret-looking keys masked and long strings cut."""
    if depth > _MAX_DEPTH:
        return "..."
    if isinstance(value, dict):
        return {str(k): (REDACTED if _SECRET_KEY.search(str(k)) else _redact(v, depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_redact(v, depth + 1) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= MAX_STRING else text[:MAX_STRING] + "..."


def encode_detail(detail) -> str | None:
    """Redacted JSON for the detail column, never longer than MAX_DETAIL bytes."""
    if detail is None:
        return None
    text = json.dumps(_redact(detail), ensure_ascii=False, separators=(",", ":"))
    if len(text.encode("utf-8")) <= MAX_DETAIL:
        return text
    keep = MAX_DETAIL - 100
    while True:
        out = json.dumps({"truncated": True, "preview": text[:keep]}, ensure_ascii=False,
                         separators=(",", ":"))
        if len(out.encode("utf-8")) <= MAX_DETAIL or keep <= 0:
            return out
        keep -= max(1, (len(out.encode("utf-8")) - MAX_DETAIL) // 2 + 1)


def _target(target, target_name):
    if target is None:
        return None, None, target_name
    if isinstance(target, tuple):
        ttype, tid, tname = (tuple(target) + (None, None, None))[:3]
        return ttype, tid, target_name or tname
    tid = getattr(target, "id", None)
    if tid is None:
        db.session.flush()  # a new object: get its id (the one error allowed through)
        tid = target.id
    tname = getattr(target, "name", None) or getattr(target, "username", None)
    return type(target).__name__.lower(), tid, target_name or tname


def _actor(user):
    """(user object or None, token id, via, ip)."""
    if has_request_context():
        api_user, api_token = g.get("api_user"), g.get("api_token")
        if api_user is not None or api_token is not None:
            return api_user, getattr(api_token, "id", None), "api", request.remote_addr
        who = user
        if current_user and current_user.is_authenticated:
            who = current_user._get_current_object()
        return who, None, "web", request.remote_addr
    via = "cli" if click.get_current_context(silent=True) is not None else "system"
    return user, None, via, None


def record(action: str, target=None, *, detail=None, user=None, target_name=None) -> None:
    """Add an audit row to the session (no commit). Never raises, except when a new
    target has to be flushed to get its id."""
    ttype, tid, tname = _target(target, target_name)
    try:
        who, token_id, via, ip = _actor(user)
        username = getattr(who, "username", None)
        db.session.add(AuditLog(
            ts=utcnow(),
            user_id=getattr(who, "id", None),
            username=username[:64] if username else None,
            via=via,
            token_id=token_id,
            action=action[:64],
            target_type=str(ttype)[:32] if ttype is not None else None,
            target_id=int(tid) if tid is not None else None,
            target_name=str(tname)[:200] if tname is not None else None,
            ip=ip[:45] if ip else None,
            detail=encode_detail(detail),
        ))
    except Exception:
        log.exception("Could not record audit action %s", action)


def record_now(action: str, target=None, **kwargs) -> None:
    """record() and commit at once. Only when nothing else is pending in the session."""
    try:
        record(action, target, **kwargs)
        db.session.commit()
    except Exception:
        db.session.rollback()
        log.exception("Could not save audit action %s", action)


def purge(days: int, batch: int = 5000) -> int:
    """Delete rows older than `days` days, in batches. Returns the number deleted."""
    cutoff = utcnow() - timedelta(days=days)
    table = AuditLog.__table__
    total = 0
    while True:
        ids = db.session.execute(
            sa.select(table.c.id).where(table.c.ts < cutoff).limit(batch)).scalars().all()
        if not ids:
            return total
        db.session.execute(sa.delete(table).where(table.c.id.in_(ids)))
        db.session.commit()
        total += len(ids)


def purge_job() -> str:
    """Job: apply audit.retention_days (0 keeps everything)."""
    days = Setting.get_int("audit.retention_days", lo=0, hi=3650)
    if not days:
        return "retention off"
    return f"deleted {purge(days)} rows older than {days} days"


__all__ = ["ACTIONS", "action_label", "encode_detail", "purge", "purge_job", "record",
           "record_now"]
