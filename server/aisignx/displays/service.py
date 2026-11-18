"""Display operations kept out of the routes. Nothing here commits; callers do."""
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy import delete, update

from ..extensions import db
from ..models import Display, PairingCode, PlaylistItem
from ..models.display import STATE_ONLINE
from ..player.broker import get_broker
from ..timeutil import iso_z, utcnow
from . import screenshots

STATE_LABELS = {
    "online": ("Online", "bg-success"),
    "offline": ("Offline", "bg-secondary"),
    "never": ("Never connected", "bg-light text-dark"),
    "unpaired": ("Unpaired", "bg-warning text-dark"),
}


def offline_after() -> timedelta:
    return timedelta(seconds=int(current_app.config["PLAYER_OFFLINE_AFTER_SECONDS"]))


def states(displays, now: datetime | None = None) -> dict[int, str]:
    """{display_id: state} for the given displays."""
    now = now or utcnow()
    limit = offline_after()
    return {d.id: d.state(now, limit) for d in displays}


def is_live(display_id: int) -> bool:
    """True when the display has an open live-update stream in this process."""
    broker = get_broker()
    return bool(broker and broker.connected(display_id))


def status_payload(now: datetime | None = None) -> dict:
    """Body of /displays/status.json."""
    now = now or utcnow()
    displays = Display.query.order_by(Display.name).all()
    current = states(displays, now)
    return {
        "server_time": iso_z(now),
        "displays": [{"id": d.id, "state": current[d.id], "last_seen": iso_z(d.last_seen_at),
                      "live": is_live(d.id), "screenshot_at": iso_z(d.screenshot_at),
                      "screenshot_pending": screenshots.pending(d, now) is not None,
                      "diagnostics_at": iso_z(d.diagnostics_at)} for d in displays],
    }


def dashboard_counts() -> tuple[int, int]:
    """(total displays, online displays). Counted in Python; there are only a few dozen."""
    displays = Display.query.all()
    online = sum(1 for s in states(displays).values() if s == STATE_ONLINE)
    return len(displays), online


def current_item_name(display: Display) -> str | None:
    """Name of the media the player last reported showing, if that item still exists."""
    if display.current_item_id is None:
        return None
    item = db.session.get(PlaylistItem, display.current_item_id)
    return item.media.name if item is not None and item.media is not None else None


def revoke_device(display: Display) -> None:
    """The old device loses everything: the privileged agent (token, key, diagnostics, codes) and every open command."""
    from ..models.device_command import OPEN_STATES, STATE_CANCELLED
    from ..models import DeviceCommand
    from . import enroll

    enroll.remove(display)
    db.session.execute(update(DeviceCommand).where(
        DeviceCommand.display_id == display.id, DeviceCommand.state.in_(OPEN_STATES))
        .values(state=STATE_CANCELLED, params=None, result="Display unpaired.")
        .execution_options(synchronize_session=False))


def unpair(display: Display) -> None:
    """Drop the display's credential. The caller commits, then calls broker.close()."""
    display.token_hash = None
    display.paired_at = None
    revoke_device(display)
    # Approved-but-unclaimed codes would otherwise re-pair the display behind our back.
    db.session.execute(delete(PairingCode).where(PairingCode.display_id == display.id)
                       .execution_options(synchronize_session=False))
