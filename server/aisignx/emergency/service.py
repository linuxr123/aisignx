"""Send and clear emergency messages. Never commits: routes commit, then call push().

push() sends the dedicated SSE events (forced, so they are never dropped); the WATCHED
commit hook separately sends a plain "manifest" hint to every screen.
"""
from ..extensions import db
from ..models import Display, EmergencyMessage
from ..models.emergency import CLEAR_MANUAL, CLEAR_TARGET_DELETED
from ..player.broker import get_broker
from ..schedules.resolve import layout_for, load_context, pick_emergency
from ..targets import applies, label, parse
from ..timeutil import utcnow

__all__ = ["send", "clear", "clear_all", "clear_for_target", "push", "layout_for", "live"]


def live() -> list[EmergencyMessage]:
    """Live messages, newest first."""
    return (EmergencyMessage.query.filter(EmergencyMessage.cleared_at.is_(None))
            .order_by(EmergencyMessage.created_at.desc(), EmergencyMessage.id.desc()).all())


def send(form, user) -> EmergencyMessage:
    """Create a live message from a validated EmergencyForm. Flushes to get an id."""
    scope, display_id, group_id = parse(form.target.data)
    playlist_id = form.playlist.data or None
    msg = EmergencyMessage(
        title=form.title.data.strip(),
        message=(form.message.data or "").strip(),
        level=form.level.data,
        # Text can only be hidden when a playlist carries the message.
        show_text=bool(form.show_text.data) or playlist_id is None,
        playlist_id=playlist_id,
        scope=scope, display_id=display_id, group_id=group_id,
        created_by_id=getattr(user, "id", None),
    )
    msg.target_label = label(msg)[:120]
    db.session.add(msg)
    db.session.flush()
    return msg


def clear(msg: EmergencyMessage, user=None, reason: str = CLEAR_MANUAL) -> bool:
    """Clear one message. Idempotent: returns False if it was already cleared."""
    if msg.cleared_at is not None:
        return False
    msg.cleared_at = utcnow()
    msg.cleared_by_id = getattr(user, "id", None)
    msg.clear_reason = reason
    return True


def clear_all(user) -> list[EmergencyMessage]:
    """Clear every live message. Returns the ones this call cleared."""
    return [m for m in live() if clear(m, user)]


def clear_for_target(display: Display | None = None, group=None) -> list[EmergencyMessage]:
    """Before deleting a display or group: clear its live messages, keeping history."""
    query = EmergencyMessage.query.filter(EmergencyMessage.cleared_at.is_(None))
    if display is not None:
        query = query.filter(EmergencyMessage.scope == "display",
                             EmergencyMessage.display_id == display.id)
    elif group is not None:
        query = query.filter(EmergencyMessage.scope == "group",
                             EmergencyMessage.group_id == group.id)
    else:
        return []
    return [m for m in query.all() if clear(m, None, CLEAR_TARGET_DELETED)]


def push(changed: EmergencyMessage) -> int:
    """After commit: tell each paired display the message targets what it shows now.

    A display that still has another live message gets that one (no flicker);
    otherwise it gets emergency_clear. Returns how many paired displays were targeted.
    """
    ctx = load_context()
    broker = get_broker()
    displays = Display.query.filter(Display.token_hash.isnot(None)).all()
    targeted = 0
    for d in displays:
        if not applies(changed, d):
            continue
        targeted += 1
        if broker is None:
            continue
        cur = pick_emergency(d, ctx.emergencies)
        if cur is not None:
            broker.publish(d.id, "emergency", cur.to_player(layout_for(cur)), force=True)
        else:
            broker.publish(d.id, "emergency_clear", {"id": changed.id}, force=True)
    return targeted
