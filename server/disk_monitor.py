"""
Disk-space monitor - Phase 2 / Task C8.

Periodic job that:
  1. Measures the upload partition's free space (shutil.disk_usage).
  2. Stores the result in `auto.disk_*` settings so admins / monitoring
     dashboards can display it without re-probing the filesystem.
  3. Emits an audit log entry the first time usage crosses
     `disk.warn_pct` (warning) or `disk.block_uploads_pct` (alert), and
     a recovery entry when usage drops back below those lines.

Upload blocking is enforced inside `storage.check_quota()` -- this module
just keeps the gauge fresh and surfaces the state changes; it never
denies a request itself.

Run from app.py boot:
    import disk_monitor
    disk_monitor.start()        # registers the periodic job

Test/operate manually:
    from disk_monitor import probe_now
    probe_now()                 # one-shot, returns the snapshot dict
"""
import logging
import os
import shutil
import time

import settings
from audit import audit
from jobs import schedule_periodic
from tenant_filter import bypass_tenant_filter


logger = logging.getLogger(__name__)


# How often the periodic probe runs. The interval is intentionally
# generous: disk usage changes slowly, and a fresh value is only needed
# when the admin opens the dashboard or an upload is processed (the
# upload path also reads the cached settings, so tighter polling buys
# nothing).
PROBE_INTERVAL_S = 300   # 5 minutes


# In-process cache of the last probe so check_quota() doesn't hit the
# settings table on every upload. None until the first probe runs.
# Tuple: (timestamp, used_bytes, total_bytes, used_pct).
_last_snapshot = None


def _upload_root() -> str:
    """Return the absolute path whose disk we monitor."""
    try:
        import upload_paths
        return str(upload_paths.resolve_upload_root())
    except Exception:
        try:
            from flask import current_app
            raw = current_app.config.get('UPLOAD_FOLDER') or 'uploads'
            p = os.path.abspath(raw)
            return p
        except Exception:
            return os.getcwd()


def _probe_disk(path: str) -> dict:
    """One snapshot. Pure function; no side effects."""
    usage = shutil.disk_usage(path)
    pct = (usage.used * 100.0 / usage.total) if usage.total else 0.0
    return {
        'path':         path,
        'total_bytes':  usage.total,
        'used_bytes':   usage.used,
        'free_bytes':   usage.free,
        'used_pct':     round(pct, 2),
        'probed_at':    time.time(),
    }


def _persist(snap: dict):
    """Mirror the snapshot into auto.* settings so it shows up in the
    admin dashboard and survives process restarts."""
    pairs = (
        ('auto.disk_total_bytes', snap['total_bytes']),
        ('auto.disk_used_bytes',  snap['used_bytes']),
        ('auto.disk_free_bytes',  snap['free_bytes']),
        ('auto.disk_used_pct',    snap['used_pct']),
        ('auto.disk_probed_at',   int(snap['probed_at'])),
    )
    for k, v in pairs:
        try:
            settings.set(k, v, is_auto=True, _allow_unknown=True)
        except Exception as e:
            logger.warning(f'disk_monitor: failed to persist {k}: {e}')


def _check_thresholds(prev: dict | None, snap: dict):
    """Audit-log threshold crossings. Compares prev vs snap and emits
    one entry per state change so we don't spam the log on every probe."""
    warn_pct  = settings.effective_value('disk.warn_pct')  or 80
    block_pct = settings.effective_value('disk.block_uploads_pct') or 95
    cur = snap['used_pct']
    old = prev['used_pct'] if prev else None

    def crossed_up(threshold):
        return (old is None or old < threshold) and cur >= threshold

    def crossed_down(threshold):
        return old is not None and old >= threshold and cur < threshold

    def notify_disk(kind, subject, body, payload):
        try:
            import alerts
            alerts.notify_event(
                kind, subject, body, payload=payload,
                target_type='disk', target_id=snap['path'],
                throttle_key=f'disk:{kind}:{snap["path"]}',
                alert_type='disk')
        except Exception as exc:
            logger.warning(f'disk_monitor: failed to notify alert system: {exc}')

    if crossed_up(block_pct):
        logger.error(f'disk_monitor: usage {cur}% >= block threshold {block_pct}% '
                     f'on {snap["path"]}; uploads will be refused')
        payload = {'used_pct': cur, 'threshold_pct': block_pct,
                   'free_bytes': snap['free_bytes']}
        audit('disk.block_threshold_crossed', target_type='disk',
              target_id=snap['path'], payload=payload)
        notify_disk(
            'disk_block_threshold_crossed',
            '[AISignX] Disk usage critical',
            f'Disk usage is {cur}% on {snap["path"]}. Uploads will be blocked at {block_pct}%.',
            payload)
    elif crossed_up(warn_pct):
        logger.warning(f'disk_monitor: usage {cur}% >= warn threshold {warn_pct}% '
                       f'on {snap["path"]}')
        payload = {'used_pct': cur, 'threshold_pct': warn_pct,
                   'free_bytes': snap['free_bytes']}
        audit('disk.warn_threshold_crossed', target_type='disk',
              target_id=snap['path'], payload=payload)
        notify_disk(
            'disk_warn_threshold_crossed',
            '[AISignX] Disk usage warning',
            f'Disk usage is {cur}% on {snap["path"]}. Warning threshold is {warn_pct}%.',
            payload)

    if crossed_down(block_pct):
        logger.info(f'disk_monitor: usage {cur}% recovered below block threshold '
                    f'{block_pct}% on {snap["path"]}; uploads re-enabled')
        payload = {'used_pct': cur, 'threshold_pct': block_pct}
        audit('disk.block_threshold_recovered', target_type='disk',
              target_id=snap['path'], payload=payload)
        notify_disk(
            'disk_block_threshold_recovered',
            '[AISignX] Disk usage recovered',
            f'Disk usage recovered to {cur}% on {snap["path"]}.',
            payload)
    elif crossed_down(warn_pct):
        logger.info(f'disk_monitor: usage {cur}% recovered below warn threshold '
                    f'{warn_pct}% on {snap["path"]}')
        payload = {'used_pct': cur, 'threshold_pct': warn_pct}
        audit('disk.warn_threshold_recovered', target_type='disk',
              target_id=snap['path'], payload=payload)
        notify_disk(
            'disk_warn_threshold_recovered',
            '[AISignX] Disk usage recovered',
            f'Disk usage recovered to {cur}% on {snap["path"]}.',
            payload)


def probe_now() -> dict:
    """Run one probe synchronously. Used by the periodic scheduler and by
    admin tools / tests that want a fresh value on demand."""
    global _last_snapshot
    path = _upload_root()
    try:
        snap = _probe_disk(path)
    except OSError as e:
        logger.error(f'disk_monitor: probe failed on {path}: {e}')
        return {}

    # Audit logging needs an app context (and a tenant context, since
    # AuditLog is tenant-scoped). The periodic worker already pushes
    # the app context but won't have a tenant -- so do this within a
    # bypass block so the audit row lands with domain_id=NULL (treated
    # as "system event" in the viewer).
    with bypass_tenant_filter():    # tenant-ok: system-wide disk event
        _check_thresholds(_last_snapshot, snap)
        _persist(snap)

    _last_snapshot = snap
    return snap


def current_snapshot() -> dict | None:
    """Return the cached snapshot from the last probe. None if the
    monitor hasn't run yet. Cheap; safe to call on every request."""
    return _last_snapshot


def is_blocking_uploads() -> tuple[bool, float | None]:
    """Return (blocking, used_pct). If True, callers should refuse new
    uploads. If we have no snapshot yet, returns (False, None) -- the
    monitor will catch up on its next tick; refusing in the meantime
    would create a self-inflicted outage on cold starts.
    """
    snap = _last_snapshot
    if snap is None:
        return False, None
    block_pct = settings.effective_value('disk.block_uploads_pct') or 95
    return (snap['used_pct'] >= block_pct), snap['used_pct']


def start():
    """Register the periodic job. Idempotent. Runs one probe immediately
    on boot so check_quota() has a snapshot before the first upload."""
    # Best-effort initial probe. If it fails (e.g. settings table not
    # ready yet), the periodic tick will retry.
    try:
        probe_now()
    except Exception as e:
        logger.warning(f'disk_monitor: initial probe failed: {e}')

    schedule_periodic(probe_now, every_s=PROBE_INTERVAL_S,
                      name='disk-monitor', first_run_delay=PROBE_INTERVAL_S)
