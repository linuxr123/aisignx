"""What a display plays right now, and why. Needs an app context, not a request.

Order (docs/scheduling.md):
1. A live emergency for the display whose playlist is playable.
2. The best active schedule for the display (engine.rank_key) with a playable playlist.
3. The display's Default playlist, if playable.
4. Nothing.

The resolver, the admin preview (week_segments) and the "Playing"/"Shadowed" badges
all go through resolve(), so they can never disagree with playback.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import selectinload

from ..models import Display, EmergencyMessage, Playlist, PlaylistItem, Schedule
from ..models.emergency import LEVEL_RANK
from ..player.manifest import is_playable
from ..targets import applies
from ..timeutil import as_utc, org_tz, utcnow
from . import engine

SOURCE_EMERGENCY = "emergency"
SOURCE_SCHEDULE = "schedule"
SOURCE_DEFAULT = "default"
SOURCE_NONE = "none"
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _source(kind: str, ident: int | None = None, name: str | None = None) -> dict:
    return {"type": kind, "id": ident, "name": name}


@dataclass(frozen=True)
class Resolution:
    playlist: Playlist | None
    source: dict  # {"type": "emergency"|"schedule"|"default"|"none", "id", "name"}
    emergency: EmergencyMessage | None

    def fingerprint(self) -> tuple:
        """What the ticker compares between minutes (names are covered by WATCHED)."""
        return (self.playlist.id if self.playlist is not None else None,
                self.source["type"], self.source["id"],
                self.emergency.id if self.emergency is not None else None)


@dataclass
class Context:
    """Everything resolve() needs that is shared across displays: one load per page/tick."""
    tz: ZoneInfo
    now: datetime
    schedules: list = field(default_factory=list)    # enabled Schedules
    emergencies: list = field(default_factory=list)  # live EmergencyMessages


@dataclass(frozen=True)
class Segment:
    """A stretch of time in the preview during which one thing plays."""
    start: datetime  # aware UTC
    end: datetime    # aware UTC, exclusive
    playlist: Playlist | None
    source: dict

    @property
    def key(self) -> tuple:
        return (self.playlist.id if self.playlist is not None else None,
                self.source["type"], self.source["id"])


def _playlist_loader(rel):
    return selectinload(rel).selectinload(Playlist.items).selectinload(PlaylistItem.media)


def load_context(now: datetime | None = None) -> Context:
    """Two queries plus the time zone setting."""
    schedules = (Schedule.query.filter(Schedule.enabled.is_(True))
                 .options(_playlist_loader(Schedule.playlist)).all())
    emergencies = (EmergencyMessage.query.filter(EmergencyMessage.cleared_at.is_(None))
                   .options(_playlist_loader(EmergencyMessage.playlist)).all())
    return Context(tz=org_tz(), now=as_utc(now) if now is not None else utcnow(),
                   schedules=schedules, emergencies=emergencies)


def is_playable_playlist(playlist) -> bool:
    return playlist is not None and any(is_playable(i) for i in playlist.items)


def pick_emergency(display, emergencies) -> EmergencyMessage | None:
    """The one live emergency this display shows: highest level, newest, highest id."""
    matching = [e for e in emergencies if e.cleared_at is None and applies(e, display)]
    if not matching:
        return None
    return max(matching, key=lambda e: (LEVEL_RANK.get(e.level, 0),
                                        as_utc(e.created_at) or _EPOCH, e.id or 0))


def layout_for(em: EmergencyMessage) -> str:
    """fullscreen: text only (no playable playlist; text always shows as a fail-safe).
    banner: the emergency playlist plays with the text over it. none: playlist only."""
    if not is_playable_playlist(em.playlist):
        return "fullscreen"
    return "banner" if em.show_text else "none"


def resolve(display: Display, now: datetime | None = None, ctx: Context | None = None, *,
            with_emergency: bool = True) -> Resolution:
    if ctx is None:
        ctx = load_context(now)
    now = as_utc(now) if now is not None else ctx.now

    em = pick_emergency(display, ctx.emergencies) if with_emergency else None
    if em is not None and is_playable_playlist(em.playlist):
        return Resolution(em.playlist, _source(SOURCE_EMERGENCY, em.id, em.title), em)

    candidates = sorted((s for s in ctx.schedules
                         if s.enabled and applies(s, display)
                         and engine.is_active(s, now, ctx.tz)),
                        key=engine.rank_key)
    for s in candidates:
        if is_playable_playlist(s.playlist):
            return Resolution(s.playlist, _source(SOURCE_SCHEDULE, s.id, s.name), em)

    if is_playable_playlist(display.playlist):
        return Resolution(display.playlist, _source(SOURCE_DEFAULT), em)
    return Resolution(None, _source(SOURCE_NONE), em)


def week_segments(display: Display, start_utc: datetime, days: int,
                  ctx: Context) -> list[Segment]:
    """What the display plays from start_utc for `days` days, ignoring emergencies.

    Probes at start, every local midnight and every boundary of the schedules that
    target the display, resolves at each, and merges neighbours that play the same
    thing. Exact, because between probes nothing can change.
    """
    start = as_utc(start_utc)
    end = start + timedelta(days=days)
    mine = [s for s in ctx.schedules if applies(s, display)]
    points = {start, *engine.boundaries(mine, start, end, ctx.tz)}
    day = start.astimezone(ctx.tz).date() + timedelta(days=1)
    while True:
        midnight = engine.local_to_utc(day, datetime.min.time(), ctx.tz)
        if midnight >= end:
            break
        points.add(midnight)
        day += timedelta(days=1)

    ordered = sorted(p for p in points if start <= p < end)
    segments: list[Segment] = []
    for i, point in enumerate(ordered):
        until = ordered[i + 1] if i + 1 < len(ordered) else end
        res = resolve(display, point, ctx, with_emergency=False)
        seg = Segment(point, until, res.playlist, res.source)
        if segments and segments[-1].key == seg.key:
            seg = Segment(segments[-1].start, until, segments[-1].playlist, segments[-1].source)
            segments[-1] = seg
        else:
            segments.append(seg)
    return segments


def split_by_day(segments: list[Segment], tz) -> list[tuple]:
    """[(local date, [(local start, local end, segment), ...]), ...] for day-grouped lists.

    A segment that crosses local midnight appears under each day it touches.
    """
    days: dict = {}
    for seg in segments:
        a, b = seg.start.astimezone(tz), seg.end.astimezone(tz)
        day = a.date()
        while True:
            next_midnight = engine.local_to_utc(day + timedelta(days=1),
                                                datetime.min.time(), tz).astimezone(tz)
            piece_start = max(a, engine.local_to_utc(day, datetime.min.time(), tz)
                              .astimezone(tz))
            piece_end = min(b, next_midnight)
            if piece_end > piece_start:
                days.setdefault(day, []).append((piece_start, piece_end, seg))
            if b <= next_midnight:
                break
            day += timedelta(days=1)
    return sorted(days.items())
