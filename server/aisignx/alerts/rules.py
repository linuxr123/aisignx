"""The alerts sweep: derive open alerts from facts other packages record.

Every rule computes the alerts that should be open now and reconciles them with the
alerts that are open: new ones open, existing ones refresh, the rest resolve. Nothing
outside this package calls into it, and because the state lives in the database the
sweep is restart-safe (a still-open alert is refreshed, never reopened or re-mailed).

Facts read: Display.last_seen_at / token_hash / alerts_muted, free disk space,
`auth.login_failed` audit rows, the backup.* Settings (written by backup/) and
jobs.status().
"""
import logging
import os
import shutil
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

import sqlalchemy as sa
from flask import current_app

from .. import jobs
from ..extensions import db
from ..filters import localtime
from ..models import AuditLog, Display, Setting
from ..timeutil import as_utc, utcnow
from . import service

log = logging.getLogger(__name__)

GB = 1024 ** 3
CRITICAL_OFFLINE = timedelta(hours=24)
STARTUP_GRACE_EXTRA_S = 120
LOGIN_WINDOW = timedelta(minutes=10)
LOGIN_QUIET = timedelta(minutes=30)
BACKUP_STALE = timedelta(hours=48)
JOB_FAIL_THRESHOLD = 3
DISK_RESOLVE_FACTOR = 1.2

_PROCESS_START = time.monotonic()


def uptime_s() -> float:
    """Seconds since this process imported the alerts package (about since start-up)."""
    return time.monotonic() - _PROCESS_START


def _reconcile(kind: str, wanted: dict, keep: set = frozenset(), opening: bool = True):
    """Open or refresh every alert in `wanted` ({key: (severity, title, detail)}) and
    resolve every open alert of `kind` that is neither wanted nor in `keep`.
    With opening=False, wanted alerts are only kept, not opened or refreshed."""
    current = service.open_keys(kind)
    if opening:
        for key, (severity, title, detail) in wanted.items():
            service.open_alert(kind, key, severity, title, detail)
    for key in current - set(wanted) - set(keep):
        service.resolve(kind, key)


# --- display_offline ---------------------------------------------------------------------

def check_displays(now) -> None:
    minutes = Setting.get_int("alerts.offline_minutes", lo=1, hi=7 * 24 * 60)
    limit = timedelta(minutes=minutes)
    wanted = {}
    rows = Display.query.filter(Display.token_hash.is_not(None),
                                Display.last_seen_at.is_not(None),
                                Display.alerts_muted.is_(False)).all()
    for display in rows:
        seen = as_utc(display.last_seen_at)
        away = now - seen
        if away < limit:
            continue
        severity = "critical" if away >= CRITICAL_OFFLINE else "warning"
        wanted[f"display:{display.id}"] = (
            severity, f"Display {display.name} is offline",
            f"Last seen {localtime(seen)} ({_ago(away)} ago).")
    # Right after a restart or a restore (whose database holds old last_seen_at times,
    # while players were told to retry later) every screen still has to reconnect: open
    # nothing new, but resolve the screens that are back (or unpaired, deleted or muted).
    grace_s = minutes * 60 + STARTUP_GRACE_EXTRA_S
    restored = Setting.get_datetime("backup.last_restore_at")
    in_grace = uptime_s() < grace_s or (
        restored is not None and (now - restored).total_seconds() < grace_s)
    _reconcile("display_offline", wanted, opening=not in_grace)


def _ago(delta: timedelta) -> str:
    minutes = int(delta.total_seconds() // 60)
    if minutes < 120:
        return f"{minutes} min"
    hours = minutes // 60
    return f"{hours} h" if hours < 48 else f"{hours // 24} days"


# --- disk_low -----------------------------------------------------------------------------

def _existing(path: Path) -> Path | None:
    """The path itself or its nearest existing parent."""
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return None


def disk_locations() -> list[tuple[str, Path]]:
    """(label, existing folder), one per file system (deduplicated by st_dev)."""
    config = current_app.config
    candidates = [("uploads", config.get("UPLOAD_FOLDER")),
                  ("backups", config.get("BACKUP_DIR"))]
    from ..dbinfo import sqlite_path
    db_file = sqlite_path()
    if db_file is not None:
        candidates.append(("database", db_file.parent))
    seen, out = set(), []
    for label, raw in candidates:
        if not raw:
            continue
        path = _existing(Path(raw))
        if path is None:
            continue
        dev = os.stat(path).st_dev
        if dev in seen:
            continue
        seen.add(dev)
        out.append((label, path))
    return out


def check_disks() -> None:
    min_bytes = Setting.get_int("alerts.disk_min_free_gb", lo=0, hi=100000) * GB
    min_pct = Setting.get_int("alerts.disk_min_free_pct", lo=0, hi=99)
    wanted, keep = {}, set()
    for label, path in disk_locations():
        usage = shutil.disk_usage(path)
        pct = 100.0 * usage.free / usage.total if usage.total else 100.0
        key = f"disk:{label}"
        if usage.free < min_bytes or pct < min_pct:
            critical = usage.free < min_bytes / 2 or pct < min_pct / 2
            wanted[key] = (
                "critical" if critical else "warning",
                f"Low disk space ({label})",
                f"{usage.free / GB:.1f} GB free of {usage.total / GB:.1f} GB ({pct:.1f}%). "
                f"Alert below {min_bytes // GB} GB or {min_pct}%.")
        elif usage.free <= min_bytes * DISK_RESOLVE_FACTOR or pct <= min_pct * DISK_RESOLVE_FACTOR:
            keep.add(key)  # hysteresis: recovered, but not by enough to resolve
    _reconcile("disk_low", wanted, keep)


# --- login_burst --------------------------------------------------------------------------

def check_logins(now) -> None:
    threshold = Setting.get_int("alerts.login_burst", lo=0, hi=100000)
    wanted, keep = {}, set()
    if threshold:
        since = now - LOGIN_WINDOW
        rows = db.session.execute(
            sa.select(AuditLog).where(AuditLog.action == "auth.login_failed",
                                      AuditLog.ts >= since)).scalars().all()
        if len(rows) >= threshold:
            ips = Counter(r.ip or "unknown" for r in rows).most_common(5)
            names = Counter(_failed_username(r) for r in rows).most_common(5)
            wanted["login:all"] = (
                "warning", f"{len(rows)} failed sign-ins in 10 minutes",
                "Top addresses: " + ", ".join(f"{ip} ({n})" for ip, n in ips) + ". "
                "Top user names: " + ", ".join(f"{u} ({n})" for u, n in names) + ".")
        else:
            last = db.session.execute(
                sa.select(sa.func.max(AuditLog.ts)).where(
                    AuditLog.action == "auth.login_failed")).scalar()
            if last is not None and now - as_utc(last) < LOGIN_QUIET:
                keep.add("login:all")
    _reconcile("login_burst", wanted, keep)


def _failed_username(row: AuditLog) -> str:
    data = row.detail_data
    name = data.get("username") if isinstance(data, dict) else None
    return str(name or row.username or "unknown")[:64]


# --- backups ------------------------------------------------------------------------------

def check_backups(now) -> None:
    ok_at = Setting.get_datetime("backup.last_ok_at")
    error_at = Setting.get_datetime("backup.last_error_at")
    failed = {}
    if error_at is not None and (ok_at is None or error_at > ok_at):
        error = (Setting.get("backup.last_error") or "unknown error")[:500]
        failed["backup"] = ("critical", "The last backup failed",
                            f"Failed at {localtime(error_at)}: {error}")
    _reconcile("backup_failed", failed)

    stale = {}
    # A server that never made a backup is not "stale"; backup_failed covers failures.
    if Setting.get_bool("backup.enabled") and ok_at is not None and now - ok_at > BACKUP_STALE:
        stale["backup"] = ("warning", "No recent backup",
                           f"The last successful backup was made {localtime(ok_at)}.")
    _reconcile("backup_stale", stale)


# --- job_failed ---------------------------------------------------------------------------

def check_jobs() -> None:
    wanted, keep = {}, set()
    for job in jobs.status():
        key = f"job:{job['name']}"
        if job["fail_count"] >= JOB_FAIL_THRESHOLD:
            wanted[key] = ("warning", f"Background job {job['name']} keeps failing",
                           f"{job['fail_count']} failures in a row. "
                           f"Last error: {job['last_error'] or 'unknown'}")
        elif job["fail_count"] > 0:
            keep.add(key)
    _reconcile("job_failed", wanted, keep)


def sweep(now=None) -> list[str]:
    """Run every rule. A failing rule never stops the others; the names of the rules
    that failed are returned."""
    now = as_utc(now) if now is not None else utcnow()
    failed = []
    for name, rule in (("displays", lambda: check_displays(now)), ("disks", check_disks),
                       ("logins", lambda: check_logins(now)),
                       ("backups", lambda: check_backups(now)), ("jobs", check_jobs)):
        try:
            rule()
        except Exception:
            db.session.rollback()
            log.exception("Alert rule %s failed", name)
            failed.append(name)
    return failed


def sweep_job() -> str:
    """Job: run the sweep; raises when a rule failed, so job_failed can report it."""
    failed = sweep()
    if failed:
        raise RuntimeError("alert rules failed: " + ", ".join(failed))
    return f"{service.open_count()} open unacknowledged"
