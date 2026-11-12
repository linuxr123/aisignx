"""Timezone-aware UTC helpers.

SQLite hands back naive datetimes even for DateTime(timezone=True) columns, so every
Python-side comparison of a stored time must go through as_utc().
"""
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_log = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime | None) -> datetime | None:
    """Return dt as an aware UTC datetime. Naive values are assumed to be UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_z(dt: datetime | None) -> str | None:
    """Format as "2026-11-11T10:00:00Z" (second precision), or None."""
    dt = as_utc(dt)
    if dt is None:
        return None
    return dt.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def org_tz() -> ZoneInfo:
    """The organization time zone (Setting "timezone"). Invalid names fall back to UTC."""
    from .models import Setting

    name = Setting.get("timezone") or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        _log.warning("Unknown time zone %r in settings; using UTC.", name)
        return ZoneInfo("UTC")


def to_local(dt: datetime | None, tz=None) -> datetime | None:
    """dt as an aware datetime in the organization time zone (or tz)."""
    if dt is None:
        return None
    return as_utc(dt).astimezone(tz or org_tz())
