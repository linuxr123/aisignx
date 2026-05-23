"""
Heartbeat batching - Phase 1, Task 7.

Player pings update Display.last_ping / status / ip_address on every request.
At scale (hundreds of displays pinging every ~25s) this is the busiest write
path in the app. Batching collects per-display state in memory and flushes
once every `heartbeat.batch_seconds` (default 60) via the jobs scheduler.

Trade-off: liveness data is up to one batch interval stale. That's fine for
"is this display online?" queries -- they already use a >120s grace window
in is_online() -- but means any code that needs sub-batch precision should
read the in-memory state via current_state(api_key) rather than hitting the
DB.

Tenant safety: heartbeats arrive WITHOUT a session cookie (player uses an
api_key in the URL). The flush task runs without tenant context, so it must
explicitly bypass the tenant filter to write to Display rows.

API
---
    record(api_key, ip=None, status='online', current_content=None)
        - in-memory write; never blocks, never raises
    flush()
        - called by the periodic scheduler; commits all pending updates
    current_state(api_key) -> dict | None
        - read latest in-memory state for one display (used by /api/displays
          listing so the UI sees fresh data even between flushes)
    install(app)
        - registers the periodic flush; idempotent
"""
import threading
import time
from datetime import datetime

from logging_config import logger
from models import db, Display
from tenant_filter import bypass_tenant_filter
import settings as settings_mod
import jobs


# {api_key: {'ip', 'status', 'current_content', 'last_ping'}}
_pending: dict = {}
_lock = threading.Lock()
_installed = False


def record(api_key: str, ip: str = None, status: str = 'online',
           current_content=None):
    """In-memory heartbeat update. Never raises, never blocks on the DB."""
    if not api_key:
        return
    now = datetime.now()
    with _lock:
        slot = _pending.get(api_key) or {}
        slot['last_ping'] = now
        if ip is not None:
            slot['ip'] = ip
        if status is not None:
            slot['status'] = status
        if current_content is not None:
            slot['current_content'] = current_content
        _pending[api_key] = slot


def current_state(api_key: str):
    """Return the latest in-memory state for one display (or None). Used by
    code paths that need fresher data than the DB has between flushes."""
    with _lock:
        v = _pending.get(api_key)
        return dict(v) if v else None


def flush():
    """Periodic job: write all pending heartbeats to the Display table.

    A single transaction handles every queued update -- one COMMIT per
    interval, regardless of how many displays pinged.
    """
    with _lock:
        if not _pending:
            return
        # Take a snapshot then reset, all under the lock so concurrent
        # record() calls aren't lost. dict() copies references; the inner
        # state dicts are immutable from this point on for the writer.
        snapshot = dict(_pending)
        _pending.clear()
    written = 0
    try:
        with bypass_tenant_filter():   # tenant-ok: cross-tenant batched flush
            for api_key, state in snapshot.items():
                disp = Display.query.filter_by(api_key=api_key).first()
                if disp is None:
                    continue
                disp.last_ping = state.get('last_ping') or datetime.now()
                if 'ip' in state:
                    disp.ip_address = state['ip']
                if 'status' in state:
                    disp.status = state['status']
                if 'current_content' in state:
                    disp.current_content = state['current_content']
                written += 1
            db.session.commit()
    except Exception as e:
        # Re-queue the snapshot so we don't lose data on transient DB errors.
        db.session.rollback()
        with _lock:
            for k, v in snapshot.items():
                _pending.setdefault(k, v)
        logger.exception(f'heartbeat.flush failed (re-queued {len(snapshot)} updates): {e}')
        return
    if written:
        logger.debug(f'heartbeat.flush: wrote {written} display heartbeats.')


def install(app):
    """Wire up the periodic flush. Reads heartbeat.batch_seconds at install
    time -- changing the setting requires a process restart to take effect."""
    global _installed
    if _installed:
        return
    with app.app_context():
        interval = settings_mod.effective_value('heartbeat.batch_seconds') or 60
    jobs.schedule_periodic(flush, every_s=int(interval),
                           name='heartbeat-flush',
                           first_run_delay=int(interval))
    _installed = True
