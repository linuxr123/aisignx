"""POST /api/player/v1/plays: proof-of-play events from a screen.

The body is {"events": [{"id", "media_id", "item_id", "started_at", "duration_ms"}]}, at
most 200 events and 64 KB. A bad event is dropped and counted, never a 4xx/5xx, so one
broken event cannot block a player's queue. Resent events are ignored by their id.
"""
import re
from datetime import datetime, timedelta

import sqlalchemy as sa
from flask import g, jsonify
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import Media, PlayEvent, Setting
from ..timeutil import as_utc, utcnow
from . import api_bp
from .api import _body, api_error
from .auth import display_required

MAX_EVENTS = 200
DISABLED_RETRY_S = 1800
MAX_AGE = timedelta(days=7)
MAX_AHEAD = timedelta(minutes=5)
MIN_DURATION_MS = 500
MAX_DURATION_MS = 86_400_000
_EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
_MAX_ID = 2**31 - 1
_CHUNK = 100  # rows per INSERT: 100 x 8 columns stays under old SQLite variable limits


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_time(value):
    """An aware ISO-8601 time as UTC, else None (naive times are refused)."""
    if not isinstance(value, str) or not 10 <= len(value) <= 40:
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return as_utc(parsed)


def clean_event(event, oldest: datetime, newest: datetime) -> dict | None:
    """The insertable values of one event, or None when it must be dropped."""
    if not isinstance(event, dict):
        return None
    event_id, media_id = event.get("id"), event.get("media_id")
    item_id, duration = event.get("item_id"), event.get("duration_ms")
    if not isinstance(event_id, str) or not _EVENT_ID.match(event_id):
        return None
    if not _is_int(media_id) or not 1 <= media_id <= _MAX_ID:
        return None
    if item_id is not None and (not _is_int(item_id) or not 0 <= item_id <= _MAX_ID):
        return None
    if not _is_int(duration):
        return None
    duration = max(0, min(MAX_DURATION_MS, duration))
    if duration < MIN_DURATION_MS:
        return None
    started = _parse_time(event.get("started_at"))
    if started is None or not oldest <= started <= newest:
        return None
    return {"client_event_id": event_id, "media_id": media_id, "item_id": item_id,
            "started_at": started, "duration_ms": duration}


def _insert(rows: list[dict]) -> int:
    """Insert, ignoring rows already stored for this display. Returns the number added."""
    dialect = db.session.get_bind().dialect.name
    if dialect in ("sqlite", "postgresql"):
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        added = 0
        for i in range(0, len(rows), _CHUNK):
            stmt = insert(PlayEvent).values(rows[i:i + _CHUNK]).on_conflict_do_nothing(
                index_elements=["display_id", "client_event_id"])
            added += max(db.session.execute(stmt).rowcount or 0, 0)
        return added
    added = 0
    for row in rows:  # other databases: one savepoint per row
        try:
            with db.session.begin_nested():
                db.session.execute(sa.insert(PlayEvent).values(**row))
            added += 1
        except IntegrityError:
            pass
    return added


@api_bp.post("/plays")
@display_required
def plays():
    if not Setting.get_bool("pop.enabled"):
        return jsonify(status="disabled", retry_after_s=DISABLED_RETRY_S)
    body = _body()
    if body is None:
        return api_error(400, "bad_request", "Expected a JSON object.")
    events = body.get("events", [])
    if not isinstance(events, list):
        return api_error(400, "bad_request", "events must be a list.")
    if len(events) > MAX_EVENTS:
        return api_error(400, "too_many_events", f"Send at most {MAX_EVENTS} events per request.")

    from ..pop.service import raw_cutoff

    now = utcnow()
    oldest = max(now - MAX_AGE, raw_cutoff(now))
    rows, seen, dropped = [], set(), 0
    for event in events:
        row = clean_event(event, oldest, now + MAX_AHEAD)
        if row is None:
            dropped += 1
        elif row["client_event_id"] not in seen:  # a repeat inside the batch is a duplicate
            seen.add(row["client_event_id"])
            rows.append(row)

    added = 0
    if rows:
        ids = {r["media_id"] for r in rows}
        names = dict(db.session.execute(
            sa.select(Media.id, Media.name).where(Media.id.in_(ids))).all())
        for row in rows:
            row.update(display_id=g.display.id, received_at=now,
                       media_name=(names.get(row["media_id"]) or None))
            if row["media_name"]:
                row["media_name"] = row["media_name"][:200]
        added = _insert(rows)
        db.session.commit()
    duplicate = len(events) - dropped - added
    return jsonify(status="ok", accepted=added, duplicate=duplicate, dropped=dropped)
