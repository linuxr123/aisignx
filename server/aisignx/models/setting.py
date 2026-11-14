"""Key/value organization settings stored in the database.

Besides the admin-edited settings in DEFAULTS, runtime status is stored here as well
(the interface between Phase 6 packages; times are ISO 8601 UTC, see set_datetime):

    job.<name>.last_run_at / last_ok_at / last_error / fail_count     written by jobs.py
    backup.last_ok_at / last_error / last_error_at / last_size / last_name /
    backup.last_scheduled_date / retry_after / last_restore_at        written by backup/
    auth.session_epoch                  replaced by a restore (models/user.py)
    pop.rollup_watermark                                              written by pop/
"""
from datetime import datetime

from ..extensions import db
from ..timeutil import as_utc

_TRUE = {"1", "true", "yes", "on"}

# Known settings and their defaults.
DEFAULTS = {
    "org_name": "My Organization",
    "timezone": "UTC",
    "default_duration": "10",
    # Audit log retention in days (0 = keep forever).
    "audit.retention_days": "365",
    # Scheduled backups.
    "backup.enabled": "1",
    "backup.hour": "3",
    "backup.keep": "7",
    "backup.include_uploads": "1",
    # Alert e-mail. The SMTP password comes from AISIGNX_SMTP_PASSWORD only.
    "smtp.host": "",
    "smtp.port": "587",
    "smtp.security": "starttls",  # starttls | ssl | none
    "smtp.username": "",
    "smtp.from": "",
    "alerts.email_enabled": "0",
    "alerts.email_to": "",  # comma-separated
    "alerts.offline_minutes": "10",
    "alerts.disk_min_free_gb": "2",
    "alerts.disk_min_free_pct": "5",
    "alerts.login_burst": "20",
    "alerts.retention_days": "90",
    # Proof of play.
    "pop.enabled": "0",
    "pop.raw_days": "14",
    "pop.daily_days": "400",
}


class Setting(db.Model):
    __tablename__ = "settings"

    key = db.Column(db.String(64), primary_key=True)
    value = db.Column(db.Text, nullable=True)

    @staticmethod
    def get(key: str, default=None):
        row = db.session.get(Setting, key)
        if row is not None and row.value is not None:
            return row.value
        return default if default is not None else DEFAULTS.get(key)

    @staticmethod
    def set(key: str, value) -> None:
        row = db.session.get(Setting, key)
        if row is None:
            row = Setting(key=key)
            db.session.add(row)
        row.value = None if value is None else str(value)

    @staticmethod
    def get_int(key: str, lo: int | None = None, hi: int | None = None,
                default: int | None = None) -> int | None:
        """The value as an int. A missing, non-numeric or out-of-range value gives the
        default (`default`, else DEFAULTS[key], else None)."""
        if default is None and DEFAULTS.get(key, "") != "":
            default = int(DEFAULTS[key])
        try:
            value = int(str(Setting.get(key, "")).strip())
        except (TypeError, ValueError):
            return default
        if (lo is not None and value < lo) or (hi is not None and value > hi):
            return default
        return value

    @staticmethod
    def get_bool(key: str) -> bool:
        return str(Setting.get(key) or "").strip().lower() in _TRUE

    @staticmethod
    def get_datetime(key: str) -> datetime | None:
        """A time stored with set_datetime (also accepts a trailing "Z"); None if unset
        or unreadable."""
        value = (Setting.get(key) or "").strip()
        if not value:
            return None
        try:
            return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return None

    @staticmethod
    def set_datetime(key: str, dt: datetime | None) -> None:
        """Store an aware UTC time as ISO 8601 ("2026-11-12T10:00:00.123456+00:00")."""
        Setting.set(key, None if dt is None else as_utc(dt).isoformat())
