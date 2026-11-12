"""Schedule time arithmetic. Pure: no Flask app, request or database needed.

Works on anything with the Schedule attributes (days_mask, start_time, end_time,
start_date, end_date, priority, scope, id), so tests can pass SimpleNamespace objects.

Rules (docs/scheduling.md):
- Windows are half-open [start, end). No times means all day (local midnight to the
  next local midnight). end < start is overnight and ends the next day.
- Weekday and date range are checked against the day an occurrence STARTS on.
- Each occurrence becomes two UTC instants. A wall time in a DST gap maps to the
  transition instant; one in a fall-back overlap maps to its first occurrence. A
  window that collapses (end <= start) on a DST day is skipped for that day.
"""
from datetime import date, datetime, time, timedelta, timezone

from ..models.schedule import SPECIFICITY
from ..timeutil import as_utc

UTC = timezone.utc
_GAP_SEARCH_MINUTES = 240  # longer than any real DST gap


def _round_trips(naive: datetime, tz) -> datetime | None:
    """The UTC instant for a local wall time, or None if that wall time doesn't exist."""
    aware = naive.replace(tzinfo=tz, fold=0)  # overlap -> first occurrence
    utc = aware.astimezone(UTC)
    if utc.astimezone(tz).replace(tzinfo=None) == naive:
        return utc
    return None


def local_to_utc(d: date, t: time, tz) -> datetime:
    """Local date + wall time in tz -> aware UTC datetime (DST policy above)."""
    naive = datetime.combine(d, t)
    found = _round_trips(naive, tz)
    if found is not None:
        return found
    probe = naive  # in a gap: walk forward to the first wall time that exists
    for _ in range(_GAP_SEARCH_MINUTES):
        probe += timedelta(minutes=1)
        found = _round_trips(probe.replace(second=0, microsecond=0), tz)
        if found is not None:
            return found
    return naive.replace(tzinfo=tz, fold=0).astimezone(UTC)  # unreachable safety net


def occurrence(s, d: date, tz) -> tuple[datetime, datetime] | None:
    """The UTC [start, end) of the occurrence that starts on local day d, if any."""
    if not (s.days_mask >> d.weekday()) & 1:
        return None
    if s.start_date and d < s.start_date:
        return None
    if s.end_date and d > s.end_date:
        return None
    if s.start_time is None or s.end_time is None:
        a = local_to_utc(d, time(0), tz)
        b = local_to_utc(d + timedelta(days=1), time(0), tz)
    else:
        end_day = d + timedelta(days=1) if s.end_time < s.start_time else d
        a = local_to_utc(d, s.start_time, tz)
        b = local_to_utc(end_day, s.end_time, tz)
    return (a, b) if b > a else None


def is_active(s, now_utc: datetime, tz) -> bool:
    """Whether the schedule's window contains now (its days/dates only, not `enabled`)."""
    now_utc = as_utc(now_utc)
    today = now_utc.astimezone(tz).date()
    for day in (today, today - timedelta(days=1)):  # yesterday: overnight windows
        occ = occurrence(s, day, tz)
        if occ is not None and occ[0] <= now_utc < occ[1]:
            return True
    return False


def rank_key(s) -> tuple:
    """Sort key: higher priority, then more specific target, then older (lower id)."""
    return (-int(s.priority or 0), -SPECIFICITY[s.scope], s.id or 0)


def boundaries(schedules, start_utc: datetime, end_utc: datetime, tz) -> list[datetime]:
    """Every occurrence start and end in [start_utc, end_utc), sorted, without duplicates."""
    start_utc, end_utc = as_utc(start_utc), as_utc(end_utc)
    if end_utc <= start_utc:
        return []
    first = start_utc.astimezone(tz).date() - timedelta(days=1)
    last = end_utc.astimezone(tz).date()
    found = set()
    for s in schedules:
        day = first
        while day <= last:
            occ = occurrence(s, day, tz)
            if occ is not None:
                found.update(t for t in occ if start_utc <= t < end_utc)
            day += timedelta(days=1)
    return sorted(found)
