"""Schedule time arithmetic (schedules/engine.py): pure, no database except the tz setting."""
import logging
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from aisignx.extensions import db
from aisignx.models import Schedule, Setting
from aisignx.models.schedule import ALL_DAYS
from aisignx.schedules import engine
from aisignx.timeutil import org_tz

UTC = timezone.utc
BERLIN = ZoneInfo("Europe/Berlin")
NEW_YORK = ZoneInfo("America/New_York")
FRI = 1 << 4
SAT = 1 << 5


def sched(start=None, end=None, days=ALL_DAYS, start_date=None, end_date=None,
          priority=0, scope="all", ident=1):
    return SimpleNamespace(start_time=start, end_time=end, days_mask=days,
                           start_date=start_date, end_date=end_date, priority=priority,
                           scope=scope, id=ident)


def z(*args) -> datetime:
    """An aware UTC datetime."""
    return datetime(*args, tzinfo=UTC)


# --- windows ----------------------------------------------------------------------------

def test_end_is_exclusive_and_back_to_back_never_overlap():
    s = sched(time(8), time(17))
    assert engine.is_active(s, z(2026, 11, 12, 8), UTC)
    assert engine.is_active(s, z(2026, 11, 12, 16, 59, 59), UTC)
    assert not engine.is_active(s, z(2026, 11, 12, 17), UTC)
    assert not engine.is_active(s, z(2026, 11, 12, 7, 59, 59), UTC)

    morning, afternoon = sched(time(8), time(12)), sched(time(12), time(17))
    for minute in range(0, 24 * 60):
        now = z(2026, 11, 12) + timedelta(minutes=minute)
        assert not (engine.is_active(morning, now, UTC) and engine.is_active(afternoon, now, UTC))
    assert engine.is_active(morning, z(2026, 11, 12, 11, 59), UTC)
    assert engine.is_active(afternoon, z(2026, 11, 12, 12), UTC)
    assert not engine.is_active(morning, z(2026, 11, 12, 12), UTC)


def test_works_with_an_unsaved_schedule_model():
    s = Schedule(name="Lunch", scope="all", days_mask=ALL_DAYS, start_time=time(12),
                 end_time=time(13), priority=0)
    assert engine.is_active(s, z(2026, 11, 12, 12, 30), UTC)
    assert not engine.is_active(s, z(2026, 11, 12, 13), UTC)
    assert s.time_label == "12:00 → 13:00"
    assert s.days_label == "Every day"


def test_overnight_window_belongs_to_the_day_it_starts():
    fri_night = sched(time(22), time(2), days=FRI)  # 2026-11-13 is a Friday
    assert date(2026, 11, 13).weekday() == 4
    assert engine.is_active(fri_night, z(2026, 11, 13, 23), UTC)
    assert engine.is_active(fri_night, z(2026, 11, 14, 1), UTC)        # Sat 01:00
    assert not engine.is_active(fri_night, z(2026, 11, 13, 1), UTC)    # Fri 01:00 (Thu night)
    assert not engine.is_active(fri_night, z(2026, 11, 14, 23), UTC)   # Sat 23:00
    assert not engine.is_active(fri_night, z(2026, 11, 14, 2), UTC)    # end is exclusive
    assert fri_night.end_time < fri_night.start_time


def test_date_range_uses_the_owning_day():
    last_night = sched(time(22), time(2), days=FRI, end_date=date(2026, 11, 13))
    assert engine.is_active(last_night, z(2026, 11, 14, 1), UTC)       # spills past end_date
    assert not engine.is_active(last_night, z(2026, 11, 21, 1), UTC)   # next Friday is out

    nightly = sched(time(22), time(2), start_date=date(2026, 11, 13))
    assert not engine.is_active(nightly, z(2026, 11, 13, 1), UTC)      # Thu night: before start
    assert engine.is_active(nightly, z(2026, 11, 13, 22), UTC)
    assert engine.is_active(nightly, z(2026, 11, 14, 1), UTC)

    dated = sched(start_date=date(2026, 11, 13), end_date=date(2026, 11, 13))
    assert engine.is_active(dated, z(2026, 11, 13, 0), UTC)
    assert not engine.is_active(dated, z(2026, 11, 12, 23, 59), UTC)
    assert not engine.is_active(dated, z(2026, 11, 14, 0), UTC)


def test_weekday_bitmask_per_day():
    monday = date(2026, 11, 9)
    assert monday.weekday() == 0
    for bit in range(7):
        s = sched(days=1 << bit)
        for offset in range(14):
            day = monday + timedelta(days=offset)
            noon = datetime.combine(day, time(12), UTC)
            assert engine.is_active(s, noon, UTC) == (day.weekday() == bit), (bit, day)
    weekdays = sched(days=0b0011111)
    assert engine.is_active(weekdays, z(2026, 11, 13, 12), UTC)       # Fri
    assert not engine.is_active(weekdays, z(2026, 11, 14, 12), UTC)   # Sat


def test_all_day_windows_chain_across_23h_and_25h_days():
    s = sched()
    for first in (date(2026, 3, 27), date(2026, 10, 23)):
        occs = [engine.occurrence(s, first + timedelta(days=i), BERLIN) for i in range(4)]
        for (_, end), (start, _) in zip(occs, occs[1:]):
            assert end == start  # no gap, no overlap
    spring = engine.occurrence(s, date(2026, 3, 29), BERLIN)
    autumn = engine.occurrence(s, date(2026, 10, 25), BERLIN)
    assert spring[1] - spring[0] == timedelta(hours=23)
    assert autumn[1] - autumn[0] == timedelta(hours=25)
    assert spring[0] == z(2026, 3, 28, 23)
    assert autumn[1] == z(2026, 10, 25, 23)


# --- time zones and DST -----------------------------------------------------------------

def test_new_york_wall_time_follows_dst():
    eight = sched(time(8), time(9))
    assert engine.is_active(eight, z(2026, 1, 15, 13), NEW_YORK)       # EST: 08:00
    assert not engine.is_active(eight, z(2026, 7, 15, 13), NEW_YORK)   # EDT: 09:00
    assert engine.is_active(eight, z(2026, 7, 15, 12), NEW_YORK)       # EDT: 08:00
    assert not engine.is_active(eight, z(2026, 3, 9, 13), NEW_YORK)    # after 2026-03-08
    assert engine.is_active(eight, z(2026, 3, 7, 13), NEW_YORK)        # before it


def test_berlin_spring_forward_gap_maps_to_transition():
    assert engine.local_to_utc(date(2026, 3, 29), time(2, 30), BERLIN) == z(2026, 3, 29, 1)
    assert engine.local_to_utc(date(2026, 3, 29), time(3), BERLIN) == z(2026, 3, 29, 1)
    assert engine.local_to_utc(date(2026, 3, 29), time(1, 59), BERLIN) == z(2026, 3, 29, 0, 59)

    s = sched(time(2, 30), time(4))
    assert engine.occurrence(s, date(2026, 3, 29), BERLIN) == (z(2026, 3, 29, 1),
                                                               z(2026, 3, 29, 2))
    assert not engine.is_active(s, z(2026, 3, 29, 0, 59), BERLIN)
    assert engine.is_active(s, z(2026, 3, 29, 1), BERLIN)


def test_berlin_fall_back_overlap_uses_first_occurrence():
    short = sched(time(2, 15), time(2, 45))
    assert engine.occurrence(short, date(2026, 10, 25), BERLIN) == (z(2026, 10, 25, 0, 15),
                                                                    z(2026, 10, 25, 0, 45))
    assert engine.is_active(short, z(2026, 10, 25, 0, 30), BERLIN)
    assert not engine.is_active(short, z(2026, 10, 25, 1, 15), BERLIN)  # 02:15 CET, again

    sat_night = sched(time(22), time(2, 30), days=SAT)
    assert date(2026, 10, 24).weekday() == 5
    assert engine.occurrence(sat_night, date(2026, 10, 24), BERLIN) == (
        z(2026, 10, 24, 20), z(2026, 10, 25, 0, 30))
    assert engine.is_active(sat_night, z(2026, 10, 25, 0, 29), BERLIN)
    for minutes in (30, 60, 89, 120):  # never resumes during the repeated hour
        assert not engine.is_active(sat_night, z(2026, 10, 25) + timedelta(minutes=minutes),
                                    BERLIN)

    one_to_two = sched(time(1), time(2))
    start, end = engine.occurrence(one_to_two, date(2026, 10, 25), BERLIN)
    assert end - start == timedelta(hours=1)
    assert start == z(2026, 10, 24, 23)


def test_window_that_collapses_on_a_dst_day_is_skipped():
    in_gap = sched(time(2), time(2, 30))
    assert engine.occurrence(in_gap, date(2026, 3, 29), BERLIN) is None
    assert not engine.is_active(in_gap, z(2026, 3, 29, 1), BERLIN)
    assert engine.occurrence(in_gap, date(2026, 3, 30), BERLIN) is not None
    assert engine.occurrence(sched(time(2, 10), time(3)), date(2026, 3, 29), BERLIN) is None


# --- precedence -------------------------------------------------------------------------

def test_rank_key():
    group_10 = sched(priority=10, scope="group", ident=5)
    display_0 = sched(priority=0, scope="display", ident=1)
    assert sorted([display_0, group_10], key=engine.rank_key)[0] is group_10

    everyone, group, display = (sched(scope=scope, ident=i)
                                for i, scope in ((1, "all"), (2, "group"), (3, "display")))
    assert sorted([everyone, group, display], key=engine.rank_key) == [display, group, everyone]

    newer, older = sched(ident=9), sched(ident=4)
    assert sorted([newer, older], key=engine.rank_key) == [older, newer]


# --- boundaries -------------------------------------------------------------------------

def test_boundaries_across_dst():
    s = sched(time(8), time(9))
    found = engine.boundaries([s], z(2026, 3, 28), z(2026, 3, 31), BERLIN)
    assert found == [z(2026, 3, 28, 7), z(2026, 3, 28, 8),     # CET
                     z(2026, 3, 29, 6), z(2026, 3, 29, 7),     # CEST from here on
                     z(2026, 3, 30, 6), z(2026, 3, 30, 7)]

    overnight = sched(time(22), time(2))
    found = engine.boundaries([overnight, s], z(2026, 10, 25, 0), z(2026, 10, 26, 0), BERLIN)
    # Sat night ends at the first 02:00 (CEST) = 00:00Z; Sun is CET from 03:00 CEST on.
    assert found == [z(2026, 10, 25, 0), z(2026, 10, 25, 7), z(2026, 10, 25, 8),
                     z(2026, 10, 25, 21)]

    # Half-open range, sorted, no duplicates, empty for an empty range.
    same = engine.boundaries([s, s], z(2026, 3, 28, 7), z(2026, 3, 28, 8), BERLIN)
    assert same == [z(2026, 3, 28, 7)]
    assert engine.boundaries([s], z(2026, 3, 28), z(2026, 3, 28), BERLIN) == []


# --- time zone setting ------------------------------------------------------------------

def test_invalid_timezone_setting_falls_back_to_utc(app, caplog):
    with app.app_context():
        assert org_tz() == ZoneInfo("UTC")  # default
        Setting.set("timezone", "Europe/Berlin")
        db.session.commit()
        assert org_tz() == BERLIN
        for bad in ("Mars/Olympus_Mons", "../etc/passwd"):
            Setting.set("timezone", bad)
            db.session.commit()
            with caplog.at_level(logging.WARNING):
                assert org_tz() == ZoneInfo("UTC")
            assert any("time zone" in r.getMessage() for r in caplog.records)
