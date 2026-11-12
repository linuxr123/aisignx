"""The resolver (schedules/resolve.py): fall-through, targets, emergencies, week preview."""
import random
from datetime import date, datetime, time, timedelta, timezone

import pytest

from aisignx.extensions import db
from aisignx.models import (
    Display, DisplayGroup, EmergencyMessage, Media, Playlist, PlaylistItem, Schedule, Setting,
)
from aisignx.player.manifest import build_manifest
from aisignx.schedules.resolve import (
    layout_for, load_context, pick_emergency, resolve, week_segments,
)

UTC = timezone.utc
NOON = datetime(2026, 11, 12, 12, tzinfo=UTC)  # a Thursday


def z(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def make_playlist(name, playable=True, empty=False) -> Playlist:
    playlist = Playlist(name=name)
    if not empty:
        url = f"https://example.com/{name}" if playable else "javascript:alert(1)"
        playlist.items.append(PlaylistItem(media=Media(name=name, media_type="web", url=url,
                                                       default_duration=10), position=0))
    db.session.add(playlist)
    return playlist


def make_schedule(name, playlist, scope="all", display=None, group=None, **kw) -> Schedule:
    s = Schedule(name=name, playlist=playlist, scope=scope,
                 display_id=display.id if display is not None else None,
                 group_id=group.id if group is not None else None, **kw)
    db.session.add(s)
    return s


def make_emergency(title, scope="all", display=None, group=None, level="critical",
                   created_at=None, playlist=None, show_text=True) -> EmergencyMessage:
    em = EmergencyMessage(title=title, level=level, scope=scope, target_label=title,
                          display_id=display.id if display is not None else None,
                          group_id=group.id if group is not None else None,
                          created_at=created_at or z(2026, 11, 12, 9),
                          playlist=playlist, show_text=show_text)
    db.session.add(em)
    return em


@pytest.fixture
def ctx_app(app):
    """An app context with one display ("Lobby", default playlist "Default")."""
    with app.app_context():
        default = make_playlist("Default")
        display = Display(name="Lobby", playlist=default, token_hash="a" * 64)
        db.session.add(display)
        db.session.commit()
        yield display


def _resolve(display, now=NOON):
    return resolve(display, now, load_context(now))


# --- fall-through -----------------------------------------------------------------------

def test_fall_through_to_next_candidate_then_default_then_none(ctx_app):
    display = ctx_app
    make_schedule("Empty", make_playlist("E", empty=True), priority=50)
    make_schedule("Broken", make_playlist("U", playable=False), priority=40)
    good = make_schedule("Good", make_playlist("P"), priority=10)
    db.session.commit()

    res = _resolve(display)
    assert res.source == {"type": "schedule", "id": good.id, "name": "Good"}
    assert res.playlist.name == "P"
    assert res.emergency is None

    good.enabled = False
    db.session.commit()
    res = _resolve(display)
    assert res.source == {"type": "default", "id": None, "name": None}
    assert res.playlist.name == "Default"

    display.playlist = make_playlist("Hollow", empty=True)
    db.session.commit()
    res = _resolve(display)
    assert res.source == {"type": "none", "id": None, "name": None}
    assert res.playlist is None
    assert res.fingerprint() == (None, "none", None, None)

    display.playlist = None
    db.session.commit()
    assert _resolve(display).source["type"] == "none"


def test_disabled_schedules_are_ignored(ctx_app):
    display = ctx_app
    make_schedule("Off", make_playlist("X"), priority=100, enabled=False)
    on = make_schedule("On", make_playlist("Y"), priority=0)
    db.session.commit()
    ctx = load_context(NOON)
    assert [s.name for s in ctx.schedules] == ["On"]
    assert resolve(display, NOON, ctx).source["id"] == on.id


def test_inactive_window_falls_back_to_default(ctx_app):
    display = ctx_app
    s = make_schedule("Lunch", make_playlist("L"), start_time=time(12), end_time=time(13))
    db.session.commit()
    assert _resolve(display, z(2026, 11, 12, 12, 30)).source["id"] == s.id
    assert _resolve(display, z(2026, 11, 12, 13)).source["type"] == "default"


def test_time_zone_setting_moves_the_window(ctx_app):
    display = ctx_app
    make_schedule("Lunch", make_playlist("L"), start_time=time(12), end_time=time(13))
    Setting.set("timezone", "Europe/Berlin")
    db.session.commit()
    assert _resolve(display, z(2026, 11, 12, 11, 30)).source["type"] == "schedule"  # 12:30 CET
    assert _resolve(display, z(2026, 11, 12, 12, 30)).source["type"] == "default"


def test_group_schedule_applies_only_to_members(ctx_app):
    member = ctx_app
    outsider = Display(name="Hall", playlist=member.playlist)
    group = DisplayGroup(name="Front")
    db.session.add_all([outsider, group])
    db.session.flush()
    member.group = group
    s = make_schedule("Group only", make_playlist("G"), scope="group", group=group)
    mine = make_schedule("Hall only", make_playlist("H"), scope="display", display=outsider)
    db.session.commit()

    assert _resolve(member).source["id"] == s.id
    assert _resolve(outsider).source["id"] == mine.id

    member.group = None
    db.session.commit()
    assert _resolve(member).source["type"] == "default"


def test_precedence_specific_beats_general_at_equal_priority(ctx_app):
    display = ctx_app
    group = DisplayGroup(name="Front")
    db.session.add(group)
    db.session.flush()
    display.group = group
    make_schedule("Everyone", make_playlist("A"))
    make_schedule("Group", make_playlist("B"), scope="group", group=group)
    mine = make_schedule("Mine", make_playlist("C"), scope="display", display=display)
    db.session.commit()
    assert _resolve(display).source["id"] == mine.id

    boosted = make_schedule("Boosted", make_playlist("D"), priority=10)
    db.session.commit()
    assert _resolve(display).source["id"] == boosted.id


# --- emergencies ------------------------------------------------------------------------

def test_pick_emergency_level_then_newest(ctx_app):
    display = ctx_app
    critical = make_emergency("Fire", level="critical", created_at=z(2026, 11, 12, 8))
    make_emergency("Rain", level="warning", created_at=z(2026, 11, 12, 10))
    db.session.commit()
    assert pick_emergency(display, load_context(NOON).emergencies) is critical

    newer = make_emergency("Flood", level="critical", created_at=z(2026, 11, 12, 9))
    db.session.commit()
    assert pick_emergency(display, load_context(NOON).emergencies) is newer

    twin = make_emergency("Twin", level="critical", created_at=z(2026, 11, 12, 9))
    db.session.commit()
    assert twin.id > newer.id
    assert pick_emergency(display, load_context(NOON).emergencies) is twin

    for em in (critical, newer, twin):
        em.cleared_at = NOON
    db.session.commit()
    assert pick_emergency(display, load_context(NOON).emergencies).title == "Rain"


def test_pick_emergency_scopes(ctx_app):
    lobby = ctx_app
    hall = Display(name="Hall")
    group = DisplayGroup(name="Front")
    db.session.add_all([hall, group])
    db.session.flush()
    lobby.group = group
    by_group = make_emergency("Group", scope="group", group=group)
    by_display = make_emergency("Hall", scope="display", display=hall)
    db.session.commit()
    ems = load_context(NOON).emergencies
    assert pick_emergency(lobby, ems) is by_group
    assert pick_emergency(hall, ems) is by_display

    everyone = make_emergency("All", created_at=z(2026, 11, 12, 11))
    db.session.commit()
    ems = load_context(NOON).emergencies
    assert pick_emergency(lobby, ems) is everyone
    assert pick_emergency(hall, ems) is everyone


def test_display_emergency_whose_display_was_deleted_matches_nothing(ctx_app):
    lobby = ctx_app
    hall = Display(name="Hall")
    db.session.add(hall)
    db.session.flush()
    em = make_emergency("Hall only", scope="display", display=hall)
    db.session.commit()
    db.session.delete(hall)
    db.session.commit()
    db.session.refresh(em)
    assert em.display_id is None and em.scope == "display"  # FK SET NULL, scope kept
    assert em.live
    assert pick_emergency(lobby, load_context(NOON).emergencies) is None


def test_emergency_playlist_overrides_schedules(app, ctx_app):
    display = ctx_app
    make_schedule("Top", make_playlist("S"), priority=100)
    em = make_emergency("Fire", playlist=make_playlist("Exit"))
    db.session.commit()
    res = _resolve(display)
    assert res.source == {"type": "emergency", "id": em.id, "name": "Fire"}
    assert res.playlist.name == "Exit"
    assert res.emergency is em
    assert layout_for(em) == "banner"
    em.show_text = False
    db.session.commit()
    assert layout_for(em) == "none"

    with app.test_request_context():
        body = build_manifest(display, NOON)
    assert body["source"]["type"] == "emergency"
    assert body["emergency"]["layout"] == "none"
    assert body["playlist"]["name"] == "Exit"


@pytest.mark.parametrize("empty", [True, False], ids=["empty", "unplayable"])
def test_unplayable_emergency_playlist_falls_back_fullscreen(app, ctx_app, empty):
    display = ctx_app
    top = make_schedule("Top", make_playlist("S"))
    em = make_emergency("Fire", playlist=make_playlist("Bad", playable=False, empty=empty),
                        show_text=False)
    db.session.commit()
    res = _resolve(display)
    assert res.source["type"] == "schedule" and res.source["id"] == top.id
    assert res.emergency is em
    assert layout_for(em) == "fullscreen"  # text shows even with show_text off
    assert res.fingerprint()[3] == em.id

    with app.test_request_context():
        body = build_manifest(display, NOON)
    assert body["emergency"]["layout"] == "fullscreen"
    assert body["emergency"]["id"] == em.id
    assert body["source"]["type"] == "schedule"


def test_text_only_emergency_keeps_normal_content(ctx_app):
    display = ctx_app
    em = make_emergency("Drill")
    db.session.commit()
    res = _resolve(display)
    assert res.source["type"] == "default"
    assert res.emergency is em
    assert layout_for(em) == "fullscreen"
    assert resolve(display, NOON, load_context(NOON), with_emergency=False).emergency is None


# --- week preview -----------------------------------------------------------------------

def _check_contiguous(segments, start, end):
    assert segments[0].start == start and segments[-1].end == end
    for a, b in zip(segments, segments[1:]):
        assert a.end == b.start
        assert a.key != b.key  # neighbours are merged


def test_week_segments_merge(ctx_app):
    display = ctx_app
    Setting.set("timezone", "Europe/Berlin")
    mornings = make_schedule("Mornings", make_playlist("Breakfast"), start_time=time(8),
                             end_time=time(12), days_mask=0b0011111)
    db.session.commit()
    start = z(2026, 11, 8, 23)  # Mon 2026-11-09 00:00 CET
    ctx = load_context(start)
    segments = week_segments(display, start, 7, ctx)
    _check_contiguous(segments, start, start + timedelta(days=7))

    assert len(segments) == 11  # default, then (mornings, default) x 5 weekdays
    kinds = [s.source["type"] for s in segments]
    assert kinds == ["default"] + ["schedule", "default"] * 5
    first_morning = segments[1]
    assert (first_morning.start, first_morning.end) == (z(2026, 11, 9, 7), z(2026, 11, 9, 11))
    assert first_morning.source["id"] == mornings.id
    # Mon 12:00 to Tue 08:00 is one segment: midnight does not split it.
    assert (segments[2].start, segments[2].end) == (z(2026, 11, 9, 11), z(2026, 11, 10, 7))
    # Fri noon to the end of the week, across the weekend.
    assert segments[-1].start == z(2026, 11, 13, 11)


def test_week_segments_ignore_emergencies(ctx_app):
    display = ctx_app
    make_emergency("Fire", playlist=make_playlist("Exit"))
    db.session.commit()
    segments = week_segments(display, NOON, 7, load_context(NOON))
    assert [s.source["type"] for s in segments] == ["default"]


def test_week_segments_match_resolve_at_random_instants(ctx_app):
    display = ctx_app
    Setting.set("timezone", "Europe/Berlin")
    group = DisplayGroup(name="Front")
    db.session.add(group)
    db.session.flush()
    display.group = group
    other = Display(name="Elsewhere")
    db.session.add(other)
    db.session.flush()
    make_schedule("Nights", make_playlist("N"), start_time=time(22), end_time=time(2, 30),
                  days_mask=0b0110000, priority=5)
    make_schedule("Work", make_playlist("W"), start_time=time(8), end_time=time(18),
                  days_mask=0b0011111, scope="group", group=group)
    make_schedule("Lunch", make_playlist("L"), start_time=time(12), end_time=time(13, 15),
                  scope="display", display=display, priority=5)
    make_schedule("Gap", make_playlist("G"), start_time=time(2, 15), end_time=time(2, 45),
                  priority=50)
    make_schedule("Event", make_playlist("V"), start_date=date(2026, 10, 24),
                  end_date=date(2026, 10, 24), priority=1)
    make_schedule("Empty", make_playlist("Z", empty=True), priority=99)
    make_schedule("Others", make_playlist("O"), priority=100, scope="display", display=other)
    make_schedule("Off", make_playlist("F"), priority=100, enabled=False)
    db.session.commit()

    start = z(2026, 10, 21, 13, 7)  # covers the 2026-10-25 fall-back night
    end = start + timedelta(days=7)
    ctx = load_context(start)
    segments = week_segments(display, start, 7, ctx)
    _check_contiguous(segments, start, end)
    assert {s.source.get("name") for s in segments} >= {"Nights", "Work", "Lunch", "Gap",
                                                        "Event", None}

    rng = random.Random(4)
    span = int((end - start).total_seconds())
    for _ in range(200):
        t = start + timedelta(seconds=rng.randrange(span))
        seg = next(s for s in segments if s.start <= t < s.end)
        res = resolve(display, t, ctx, with_emergency=False)
        assert seg.key == (res.playlist.id if res.playlist else None,
                           res.source["type"], res.source["id"]), t
