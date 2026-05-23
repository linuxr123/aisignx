"""
Schedule conflict analyser.

Walks the current tenant's schedules, projects them across the next N
days, and reports overlapping windows per affected display so operators
can see when two or more schedules want the same display at the same
time. The same priority + id ordering used by `_resolve_playlist` picks
the winner; everything else in an overlap is shown as "shadowed".
"""
from __future__ import annotations

from datetime import date, time, timedelta
from typing import Iterable

from models import Schedule, Display, Playlist
from groups import resolve_effective_group_ids


# Sentinel times: if a schedule has no start/end time it covers the
# full local day.
_DAY_START = time(0, 0, 0)
_DAY_END   = time(23, 59, 59)


def _parse_dow(raw: str | None) -> set[int] | None:
    """Return the set of ISO weekdays (1-7) the schedule runs on, or
    None if it runs every day."""
    if not raw:
        return None
    out: set[int] = set()
    for tok in str(raw).split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            n = int(tok)
        except ValueError:
            continue
        if 1 <= n <= 7:
            out.add(n)
    return out or None


def _runs_on(sched: Schedule, day: date, dow_cache: dict) -> bool:
    if sched.start_date and sched.start_date > day:
        return False
    if sched.end_date and sched.end_date < day:
        return False
    dow = dow_cache.get(sched.id)
    if dow is None:
        dow = _parse_dow(sched.days_of_week)
        dow_cache[sched.id] = dow or set()
    if dow:
        if day.isoweekday() not in dow:
            return False
    return True


def _windows_for(sched: Schedule) -> list[tuple[time, time]]:
    """Return one or two (start, end) pairs covering this schedule's
    time-of-day. Wrap-around (e.g. 22:00-06:00) splits into two."""
    s = sched.start_time
    e = sched.end_time
    if not s and not e:
        return [(_DAY_START, _DAY_END)]
    s = s or _DAY_START
    e = e or _DAY_END
    if s <= e:
        return [(s, e)]
    # Wrap across midnight: split into [s, 23:59:59] + [00:00, e]
    return [(s, _DAY_END), (_DAY_START, e)]


def _seconds(t: time) -> int:
    return t.hour * 3600 + t.minute * 60 + t.second


def _overlap(a: tuple[time, time], b: tuple[time, time]) -> tuple[time, time] | None:
    start = max(a[0], b[0])
    end   = min(a[1], b[1])
    if _seconds(end) <= _seconds(start):
        return None
    return (start, end)


def _displays_for(sched: Schedule, all_displays: list[Display],
                  group_chain_cache: dict) -> list[Display]:
    """Resolve which displays a schedule actually targets in this
    tenant, honoring schedule inheritance through the group hierarchy."""
    if sched.display_id:
        return [d for d in all_displays if d.id == sched.display_id]
    if sched.group_id is None:
        return []
    out = []
    for d in all_displays:
        chain = group_chain_cache.get(d.id)
        if chain is None:
            chain = set(resolve_effective_group_ids(d))
            group_chain_cache[d.id] = chain
        if sched.group_id in chain:
            out.append(d)
    return out


def compute_conflicts(days_ahead: int = 7,
                      domain_id: int | None = None) -> dict:
    """Return a per-display conflict report covering today plus the
    next `days_ahead - 1` days.

    The current tenant context is honored automatically via the SQLAlchemy
    tenant filter; pass `domain_id` only when calling from a context that
    needs an explicit override.
    """
    days_ahead = max(1, min(int(days_ahead or 7), 30))

    sched_q = Schedule.query.filter(Schedule.is_active == True)
    if domain_id is not None:
        sched_q = sched_q.filter(Schedule.domain_id == domain_id)
    schedules: list[Schedule] = sched_q.all()

    disp_q = Display.query
    if domain_id is not None:
        disp_q = disp_q.filter(Display.domain_id == domain_id)
    displays: list[Display] = disp_q.all()

    # Pre-fetch playlist names for the summary payload.
    pl_ids = {s.playlist_id for s in schedules if s.playlist_id}
    pl_map = {p.id: p.name for p in Playlist.query.filter(Playlist.id.in_(pl_ids)).all()} \
             if pl_ids else {}

    dow_cache: dict[int, set[int]] = {}
    chain_cache: dict[int, set[int]] = {}

    # Group schedules per display.
    per_display: dict[int, list[tuple[Schedule, str]]] = {d.id: [] for d in displays}
    for s in schedules:
        targets = _displays_for(s, displays, chain_cache)
        if s.display_id:
            source = 'display'
        else:
            source = f'group:{s.group_id}'
        for d in targets:
            per_display[d.id].append((s, source))

    today = date.today()
    days = [today + timedelta(days=i) for i in range(days_ahead)]

    report_displays = []
    total_conflicts = 0

    for d in displays:
        bucket = per_display.get(d.id) or []
        if not bucket:
            continue

        sched_list = [{
            'id':           s.id,
            'name':         s.name,
            'playlist_id':  s.playlist_id,
            'playlist_name': pl_map.get(s.playlist_id),
            'priority':     s.priority or 0,
            'days_of_week': s.days_of_week or '',
            'start_time':   s.start_time.isoformat() if s.start_time else None,
            'end_time':     s.end_time.isoformat()   if s.end_time   else None,
            'start_date':   s.start_date.isoformat() if s.start_date else None,
            'end_date':     s.end_date.isoformat()   if s.end_date   else None,
            'source':       source,
        } for (s, source) in bucket]

        # Detect overlaps day-by-day.
        day_conflicts = []
        for day in days:
            active: list[tuple[time, time, Schedule]] = []
            for (s, _src) in bucket:
                if not _runs_on(s, day, dow_cache):
                    continue
                for win in _windows_for(s):
                    active.append((win[0], win[1], s))
            if len(active) < 2:
                continue
            # Pairwise overlap detection (small N per display in practice).
            seen_pairs: set[tuple[int, int]] = set()
            for i in range(len(active)):
                for j in range(i + 1, len(active)):
                    a_s, a_e, a_sched = active[i]
                    b_s, b_e, b_sched = active[j]
                    if a_sched.id == b_sched.id:
                        continue
                    ov = _overlap((a_s, a_e), (b_s, b_e))
                    if not ov:
                        continue
                    key = tuple(sorted((a_sched.id, b_sched.id)))
                    if key in seen_pairs:
                        continue
                    seen_pairs.add(key)
                    pair = sorted((a_sched, b_sched),
                                  key=lambda s: (-(s.priority or 0), s.id))
                    day_conflicts.append({
                        'day':       day.isoformat(),
                        'start':     ov[0].isoformat(),
                        'end':       ov[1].isoformat(),
                        'schedule_ids': [pair[0].id, pair[1].id],
                        'winner_id': pair[0].id,
                        'shadowed_id': pair[1].id,
                    })

        if day_conflicts:
            total_conflicts += len(day_conflicts)

        report_displays.append({
            'id':         d.id,
            'name':       d.name,
            'group_id':   getattr(d, 'group_id', None),
            'schedules':  sched_list,
            'conflicts':  day_conflicts,
        })

    # Stable order: displays with conflicts first, then by name.
    report_displays.sort(key=lambda r: (0 if r['conflicts'] else 1,
                                        (r['name'] or '').lower()))

    return {
        'days_ahead':     days_ahead,
        'from':           today.isoformat(),
        'to':             (today + timedelta(days=days_ahead - 1)).isoformat(),
        'displays':       report_displays,
        'total_displays': len(report_displays),
        'total_conflicts': total_conflicts,
    }
