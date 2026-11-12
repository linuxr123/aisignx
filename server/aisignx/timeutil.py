"""Timezone-aware UTC helpers.

SQLite hands back naive datetimes even for DateTime(timezone=True) columns, so every
Python-side comparison of a stored time must go through as_utc().
"""
from datetime import datetime, timezone


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
