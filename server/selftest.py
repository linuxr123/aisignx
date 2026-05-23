"""
Operator-runnable self-tests.

A small suite of read-only-ish health checks that verify the server is
actually able to do its job, beyond just the cheap "is the DB connected?"
probe in /api/system/health. These are designed to be safe to run on a
live system: any artifacts created (test files, audit rows) are cleaned
up, the test webhook target is the configured one (so operators see real
deliverability), and nothing touches Display rows or Proof-of-Play data.

Each check returns a dict:
    {'name': str, 'ok': bool, 'detail': str, 'duration_ms': int}

Aggregate result:
    {'ok': bool, 'checks': [...], 'ran_at': iso8601, 'duration_ms': int}
"""
from __future__ import annotations

import os
import sys
import time
import uuid
import shutil
import logging
import platform
import tempfile
import datetime as _dt
from typing import Callable

logger = logging.getLogger(__name__)


def _timed(fn: Callable[[], tuple[bool, str]]) -> dict:
    name = fn.__name__.replace('check_', '').replace('_', ' ')
    t0 = time.perf_counter()
    try:
        ok, detail = fn()
    except Exception as exc:
        ok, detail = False, f'exception: {exc!r}'
    return {
        'name':        name,
        'ok':          bool(ok),
        'detail':      detail,
        'duration_ms': int((time.perf_counter() - t0) * 1000),
    }


# ── Individual checks ───────────────────────────────────────────────────────
def check_database_write():
    """Round-trip an audit row to confirm the DB is writable, then delete it."""
    from models import db, AuditLog
    marker = f'selftest:{uuid.uuid4()}'
    row = AuditLog(action='selftest.ping', target_type='selftest',
                   target_id=marker, payload={'marker': marker})
    db.session.add(row)
    db.session.commit()
    rid = row.id
    # Best-effort cleanup; AuditLog is append-only by convention but a
    # selftest row should not pollute the operator's audit history.
    try:
        AuditLog.query.filter_by(id=rid).delete()
        db.session.commit()
    except Exception:
        db.session.rollback()
    return True, f'wrote+deleted audit row id={rid}'


def check_disk_writable():
    """Confirm the upload/static directories accept writes."""
    from flask import current_app
    candidates = []
    for cfg_key in ('UPLOAD_FOLDER', 'STATIC_FOLDER', 'BACKUP_FOLDER'):
        v = current_app.config.get(cfg_key)
        if v:
            candidates.append((cfg_key, v))
    if not candidates:
        candidates.append(('static', current_app.static_folder or 'static'))
    failures = []
    written = []
    for label, path in candidates:
        try:
            os.makedirs(path, exist_ok=True)
            with tempfile.NamedTemporaryFile(prefix='selftest_', dir=path,
                                             delete=False) as f:
                f.write(b'selftest')
                tmp = f.name
            os.unlink(tmp)
            written.append(label)
        except Exception as exc:
            failures.append(f'{label}: {exc}')
    if failures:
        return False, '; '.join(failures)
    return True, 'wrote+removed test file in: ' + ', '.join(written)


def check_disk_free_space():
    """Warn if the data volume is below 1 GB free."""
    from flask import current_app
    target = current_app.config.get('UPLOAD_FOLDER') or current_app.static_folder or '.'
    usage = shutil.disk_usage(target)
    gb_free = usage.free / (1024 ** 3)
    ok = gb_free >= 1.0
    return ok, f'{gb_free:.2f} GB free at {target}'


def check_jobs_alive():
    """Confirm the background scheduler thread is still running."""
    try:
        import jobs
    except Exception as exc:
        return False, f'jobs module not importable: {exc}'
    # The jobs module exposes its scheduler thread via _scheduler_thread
    # if it's been started. Older instances may not have the attribute.
    th = getattr(jobs, '_scheduler_thread', None)
    if th is None:
        return False, 'scheduler thread not registered'
    return th.is_alive(), f'scheduler alive={th.is_alive()}'


def check_alerts_configured():
    """Report (not fail) on whether at least one alert channel is set."""
    try:
        import settings as _s
    except Exception as exc:
        return False, f'settings unavailable: {exc}'
    enabled = bool(_s.effective_value('alerts.enabled'))
    recipients = _s.effective_value('alerts.user_recipients') or {}
    has_assigned = bool(recipients.get('global_user_ids') or
                        recipients.get('tenant_user_ids'))
    has_email = bool(has_assigned and _s.effective_value('alerts.smtp_host'))
    has_hook  = bool(_s.effective_value('alerts.webhook_url'))
    if not enabled:
        return True, 'alerts disabled (no channels needed)'
    if has_email or has_hook:
        chans = ', '.join(c for c, on in (('email', has_email), ('webhook', has_hook)) if on)
        return True, f'enabled with: {chans}'
    return False, 'alerts enabled but no email/webhook channel configured'


def check_webhook_reachable():
    """If a webhook URL is configured, try a HEAD/POST to confirm DNS+TCP."""
    try:
        import settings as _s
    except Exception as exc:
        return False, f'settings unavailable: {exc}'
    url = (_s.effective_value('alerts.webhook_url') or '').strip()
    if not url:
        return True, 'no webhook configured (skipped)'
    try:
        import urllib.request as _u
        req = _u.Request(url, data=b'{"event":"selftest"}',
                         headers={'Content-Type': 'application/json'},
                         method='POST')
        with _u.urlopen(req, timeout=5) as resp:
            return (200 <= resp.status < 400), f'POST → HTTP {resp.status}'
    except Exception as exc:
        return False, f'POST failed: {exc}'


def check_email_smtp_handshake():
    """Open an SMTP connection (no send) to confirm host/port/STARTTLS."""
    try:
        import settings as _s
    except Exception as exc:
        return False, f'settings unavailable: {exc}'
    host = (_s.effective_value('alerts.smtp_host') or '').strip()
    if not host:
        return True, 'no SMTP host configured (skipped)'
    try:
        port = int(_s.effective_value('alerts.smtp_port') or 587)
    except (TypeError, ValueError):
        port = 587
    use_tls = bool(_s.effective_value('alerts.smtp_starttls'))
    try:
        import smtplib, socket
        with smtplib.SMTP(host, port, timeout=5) as s:
            s.ehlo()
            if use_tls:
                s.starttls()
                s.ehlo()
        return True, f'connected {host}:{port} (starttls={use_tls})'
    except (socket.gaierror, OSError, smtplib.SMTPException) as exc:
        return False, f'connection failed: {exc}'


def check_python_runtime():
    """Report the runtime stack so the page is also useful for support."""
    return True, (f'Python {platform.python_version()} on '
                  f'{platform.system()} {platform.release()} '
                  f'({platform.machine()})')


# ── Aggregator ──────────────────────────────────────────────────────────────
ALL_CHECKS = [
    check_python_runtime,
    check_database_write,
    check_disk_writable,
    check_disk_free_space,
    check_jobs_alive,
    check_alerts_configured,
    check_webhook_reachable,
    check_email_smtp_handshake,
]


def run_all() -> dict:
    t0 = time.perf_counter()
    results = [_timed(fn) for fn in ALL_CHECKS]
    return {
        'ok':          all(r['ok'] for r in results),
        'ran_at':      _dt.datetime.utcnow().isoformat() + 'Z',
        'duration_ms': int((time.perf_counter() - t0) * 1000),
        'checks':      results,
    }
