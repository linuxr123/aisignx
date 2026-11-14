"""Proof of play: daily rollup, retention and report queries.

Screens post raw PlayEvent rows (player/plays.py). Once an hour `rollup_job` rebuilds the
PlayDaily rows (plays per local day, display and media in the organization time zone) for
every day that received new events, then purges old rows. Reports read PlayDaily only.

A day is rebuilt from raw rows, so only days still fully inside the raw window
(`pop.raw_days`) are rebuilt; ingest drops events older than that window for the same reason.
"""
import logging
from datetime import date, datetime, time, timedelta, timezone

import sqlalchemy as sa

from ..extensions import db
from ..models import Display, PlayDaily, PlayEvent, Setting
from ..timeutil import org_tz, to_local, utcnow

log = logging.getLogger(__name__)

WATERMARK = "pop.rollup_watermark"
PURGE_BATCH = 5000
# The watermark is set a little before the job start, so an event whose request began
# before the job but committed during it is still picked up next time. Rebuilds are
# idempotent, so the overlap costs nothing.
WATERMARK_MARGIN = timedelta(minutes=2)
GROUPS = ("media", "display", "day")


def raw_days() -> int:
    return Setting.get_int("pop.raw_days", lo=1, hi=90) or 14


def daily_days() -> int:
    return Setting.get_int("pop.daily_days", lo=30, hi=3650) or 400


def day_bounds(day: date, tz) -> tuple[datetime, datetime]:
    """The UTC instants where a local day starts and the next one starts."""
    start = datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz).astimezone(timezone.utc)
    return start, end


def raw_first_day(now: datetime | None = None, tz=None) -> date:
    """The oldest local day whose raw events are all kept."""
    tz = tz or org_tz()
    return to_local(now or utcnow(), tz).date() - timedelta(days=raw_days())


def raw_cutoff(now: datetime | None = None, tz=None) -> datetime:
    """Raw events that started before this UTC instant are purged (and not accepted)."""
    tz = tz or org_tz()
    return day_bounds(raw_first_day(now, tz), tz)[0]


# --- rollup -------------------------------------------------------------------------------

def rebuild_day(day: date, tz) -> int:
    """Replace the PlayDaily rows of one local day from the raw events. No commit.

    Only the rows of displays with raw events that day are replaced. Deleting a display
    cascades its raw events away, so the rows it already has are kept as they are: they
    are the only record left of its plays (also when a new display reuses its id later).
    """
    start, end = day_bounds(day, tz)
    with_events = sa.select(PlayEvent.display_id).where(
        PlayEvent.started_at >= start, PlayEvent.started_at < end)
    db.session.execute(sa.delete(PlayDaily).where(
        PlayDaily.day == day, PlayDaily.display_id.in_(with_events)))
    fallback = sa.literal("Display #") + sa.cast(PlayEvent.display_id, sa.String)
    sel = (
        sa.select(
            sa.literal(day, sa.Date),
            PlayEvent.display_id,
            PlayEvent.media_id,
            sa.func.coalesce(sa.func.max(Display.name), sa.func.max(fallback)),
            sa.func.max(PlayEvent.media_name),
            sa.func.count(),
            sa.cast(sa.func.coalesce(sa.func.sum(PlayEvent.duration_ms), 0) / 1000, sa.Integer),
        )
        .select_from(PlayEvent)
        .outerjoin(Display, Display.id == PlayEvent.display_id)
        .where(PlayEvent.started_at >= start, PlayEvent.started_at < end)
        .group_by(PlayEvent.display_id, PlayEvent.media_id)
    )
    cols = ["day", "display_id", "media_id", "display_name", "media_name", "plays", "seconds"]
    result = db.session.execute(sa.insert(PlayDaily).from_select(cols, sel))
    return max(result.rowcount or 0, 0)


def rollup(now: datetime | None = None) -> str:
    """Rebuild the days with new events, move the watermark, then purge. Commits."""
    now = now or utcnow()
    tz = org_tz()
    today = to_local(now, tz).date()
    first = today - timedelta(days=1)

    query = sa.select(sa.func.min(PlayEvent.started_at))
    watermark = Setting.get_datetime(WATERMARK)
    if watermark is not None:
        query = query.where(PlayEvent.received_at >= watermark)
    earliest = db.session.scalar(query)
    if earliest is not None:
        first = min(first, to_local(earliest, tz).date())
    first = max(first, raw_first_day(now, tz))  # older days lack some raw events

    days = 0
    day = first
    while day <= today:
        rebuild_day(day, tz)
        db.session.commit()
        days += 1
        day += timedelta(days=1)
    Setting.set_datetime(WATERMARK, now - WATERMARK_MARGIN)
    db.session.commit()

    raw = purge_raw(raw_cutoff(now, tz))
    daily = purge_daily(today - timedelta(days=daily_days()))
    return f"rebuilt {days} day(s); purged {raw} raw and {daily} daily rows"


def keep_plays_of(display_id: int) -> None:
    """Call before deleting a display: the delete cascades its raw events away, so the
    plays since the last hourly rollup are rolled up first. Commits; never raises."""
    try:
        pending = db.session.scalar(
            sa.select(PlayEvent.id).where(PlayEvent.display_id == display_id).limit(1))
        if pending is not None:
            rollup()
    except Exception:
        db.session.rollback()
        log.exception("Could not roll up the plays of display %s before deleting it",
                      display_id)


def rollup_job() -> str:
    """Hourly job (jobs.py)."""
    return rollup()


def purge_raw(before: datetime) -> int:
    """Delete raw events that started before `before`, in batches. Commits."""
    total = 0
    while True:
        ids = db.session.scalars(
            sa.select(PlayEvent.id).where(PlayEvent.started_at < before).limit(PURGE_BATCH)
        ).all()
        if not ids:
            return total
        db.session.execute(sa.delete(PlayEvent).where(PlayEvent.id.in_(ids)))
        db.session.commit()
        total += len(ids)


def purge_daily(before: date) -> int:
    """Delete daily rows for days before `before`, one day per transaction. Commits."""
    total = 0
    old_days = db.session.scalars(
        sa.select(PlayDaily.day).where(PlayDaily.day < before).distinct()
    ).all()
    for day in old_days:
        result = db.session.execute(sa.delete(PlayDaily).where(PlayDaily.day == day))
        db.session.commit()
        total += max(result.rowcount or 0, 0)
    return total


# --- reports ------------------------------------------------------------------------------

def _daily_filter(query, start: date, end: date, display_ids, media_id):
    query = query.where(PlayDaily.day >= start, PlayDaily.day <= end)
    if display_ids is not None:
        query = query.where(PlayDaily.display_id.in_(list(display_ids) or [-1]))
    if media_id is not None:
        query = query.where(PlayDaily.media_id == media_id)
    return query


def report(start: date, end: date, group_by: str = "media", display_ids=None,
           media_id: int | None = None) -> dict:
    """Totals from PlayDaily between two local days (inclusive).

    display_ids: None for every screen, else an iterable of display ids.
    Returns {"rows": [{key, label, plays, seconds, screens}], "total": {...}}.
    """
    if group_by not in GROUPS:
        group_by = "media"
    measures = (
        sa.func.sum(PlayDaily.plays).label("plays"),
        sa.func.sum(PlayDaily.seconds).label("seconds"),
        sa.func.count(sa.distinct(PlayDaily.display_id)).label("screens"),
    )
    if group_by == "media":
        key, label = PlayDaily.media_id, sa.func.max(PlayDaily.media_name)
    elif group_by == "display":
        key, label = PlayDaily.display_id, sa.func.max(PlayDaily.display_name)
    else:
        key, label = PlayDaily.day, sa.func.max(PlayDaily.day)
    query = _daily_filter(sa.select(key.label("key"), label.label("label"), *measures),
                          start, end, display_ids, media_id).group_by(key)
    query = query.order_by(key) if group_by == "day" else query.order_by(
        sa.desc("plays"), key)

    rows = []
    for row in db.session.execute(query):
        text = row.label
        if group_by == "media" and not text:
            text = f"Media #{row.key}"
        elif group_by == "display" and not text:
            text = f"Display #{row.key}"
        elif group_by == "day":
            text = row.key.isoformat() if isinstance(row.key, date) else str(row.key)
        rows.append({"key": row.key, "label": text, "plays": int(row.plays or 0),
                     "seconds": int(row.seconds or 0), "screens": int(row.screens or 0)})

    total = db.session.execute(_daily_filter(sa.select(*measures), start, end,
                                             display_ids, media_id)).one()
    return {"rows": rows, "group_by": group_by,
            "total": {"plays": int(total.plays or 0), "seconds": int(total.seconds or 0),
                      "screens": int(total.screens or 0)}}


def daily_rows(start: date, end: date, display_ids=None, media_id: int | None = None):
    """PlayDaily rows for the aggregate CSV, ordered by day, display and media."""
    query = _daily_filter(sa.select(PlayDaily), start, end, display_ids, media_id)
    query = query.order_by(PlayDaily.day, PlayDaily.display_id, PlayDaily.media_id)
    return db.session.scalars(query.execution_options(yield_per=500))


def raw_rows(start: date, end: date, display_ids=None, media_id: int | None = None, tz=None):
    """Raw (PlayEvent, display name) rows between two local days, oldest first."""
    tz = tz or org_tz()
    lo = day_bounds(start, tz)[0]
    hi = day_bounds(end, tz)[1]
    query = (sa.select(PlayEvent, Display.name)
             .outerjoin(Display, Display.id == PlayEvent.display_id)
             .where(PlayEvent.started_at >= lo, PlayEvent.started_at < hi))
    if display_ids is not None:
        query = query.where(PlayEvent.display_id.in_(list(display_ids) or [-1]))
    if media_id is not None:
        query = query.where(PlayEvent.media_id == media_id)
    query = query.order_by(PlayEvent.started_at, PlayEvent.id)
    return db.session.execute(query.execution_options(yield_per=500))
