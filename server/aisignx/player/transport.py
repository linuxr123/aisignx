"""Group transport: next, previous, fast forward, rewind and pause for a whole synchronized group.

A synchronized group plays a timeline worked out from the shared server clock (static/player/sync.js).
A screen of the group that uses a control does not leave that timeline: it moves the timeline for
everybody. The state is small and lives on the group:

    {"rev": 7, "key": "0:12=10000,15=8000", "offset_ms": 1790000000123,
     "paused_ms": null, "resume_at_ms": null}

* `key`: the playlist the state was made for (the player's own sync key). A screen that plays
  another playlist ignores the state.
* `offset_ms`: the timeline position at server time T is `T - offset_ms`.
* `paused_ms`: set while paused: the timeline position the group is frozen at.
* `resume_at_ms`: server time at which a pause ends by itself (the display's "Resume after").
  The timeline then goes on from the frozen position, so nobody has to remember anything.

The player works out the new state (it knows the durations); the server only checks, numbers
(`rev`), stores and tells the group's screens to fetch the manifest, which carries the state.
"""
import json

from flask import g, jsonify
from sqlalchemy import select, update

from ..extensions import db
from ..models import Display, DisplayGroup
from . import api_bp
from .api import _body, _bad, api_error
from .auth import display_required
from .broker import get_broker

MAX_KEY = 4000
MAX_MS = 10**14  # far beyond any real timestamp: a bound, not a rule


def _number(value):
    """An int within bounds, or None (also for bool and non-numbers)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != value or abs(value) > MAX_MS:  # NaN or absurd
        return None
    return int(value)


def load(group) -> dict | None:
    """The stored state of a group, or None when there is none (or it is damaged)."""
    if group is None or not group.transport:
        return None
    try:
        data = json.loads(group.transport)
    except ValueError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("key"), str):
        return None
    return data


@api_bp.post("/group/transport")
@display_required
def set_transport():
    """A screen of a synchronized group moved the group's timeline (see the module docstring)."""
    group = g.display.group
    if group is None or not group.sync_playback:
        return api_error(409, "conflict", "This screen is not in a group with synchronized playback.")
    if group.sync_controls is False:
        return api_error(409, "conflict", "Controls do not move the whole group: this group's option is off.")
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    key, offset = body.get("key"), _number(body.get("offset_ms"))
    if not isinstance(key, str) or not key or len(key) > MAX_KEY:
        return _bad("Invalid key.")
    if offset is None:
        return _bad("Invalid offset_ms.")
    paused, resume_at = body.get("paused_ms"), body.get("resume_at_ms")
    if paused is None:
        resume_at = None
    else:
        paused, resume_at = _number(paused), _number(resume_at)
        if paused is None or resume_at is None:
            return _bad("A pause needs paused_ms and resume_at_ms.")

    old = load(group)
    state = {"rev": (int(old.get("rev", 0)) if old else 0) + 1, "key": key, "offset_ms": offset,
             "paused_ms": paused, "resume_at_ms": resume_at}
    group_id = group.id
    # A Core UPDATE: no ORM flush, so no manifest broadcast to every screen (only the group is told, below).
    db.session.execute(
        update(DisplayGroup).where(DisplayGroup.id == group_id)
        .values(transport=json.dumps(state, separators=(",", ":")))
        .execution_options(synchronize_session=False)
    )
    ids = list(db.session.scalars(select(Display.id).where(Display.group_id == group_id)))
    db.session.commit()
    broker = get_broker()
    if broker is not None:
        for display_id in ids:
            broker.publish(display_id, "manifest", {})  # the manifest carries the state
    return jsonify(transport=state)
