"""Push schedule boundaries to screens: one daemon thread per process.

About 0.5 s after every wall-clock minute the ticker resolves every paired display and
sends a "manifest" hint to those whose resolution changed since the last tick. It does
no boundary arithmetic of its own, so it also catches date rollovers, DST, time zone
changes and emergencies sent from other processes. The first pass only records the
current state, so a restart causes no reconnect herd.

The ticker and the broker are per process. Screens served by another process still
converge through the 30 s heartbeat. It is read-only and never commits.
"""
import threading
import time
from datetime import datetime

from flask import Flask

from ..models import Display
from .broker import get_broker

_KEY = "aisignx_schedule_ticker"
_lock = threading.Lock()


def tick(app: Flask, last: dict, now: datetime | None = None) -> list[int]:
    """One pass. `last` maps display id -> fingerprint and is updated in place.

    Returns the ids of the displays that were notified.
    """
    from ..schedules.resolve import load_context, resolve

    with app.app_context():
        ctx = load_context(now)
        displays = Display.query.filter(Display.token_hash.isnot(None)).all()
        changed = []
        for d in displays:
            fp = resolve(d, ctx.now, ctx).fingerprint()
            if d.id in last and last[d.id] != fp:
                changed.append(d.id)
            last[d.id] = fp
        for gone in set(last) - {d.id for d in displays}:
            del last[gone]
        broker = get_broker()
        if broker is not None:
            for display_id in changed:
                broker.publish(display_id, "manifest", {})
        return changed


def ensure_started(app: Flask) -> None:
    """Start the ticker thread unless it runs already. Restarts a dead thread."""
    if not app.config.get("PLAYER_SCHEDULE_TICKER"):
        return
    with _lock:
        thread = app.extensions.get(_KEY)
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(target=_run, args=(app,), name="aisignx-schedule-ticker",
                                  daemon=True)
        app.extensions[_KEY] = thread
        thread.start()


def _sleep_to_next_minute() -> None:
    time.sleep(60.5 - time.time() % 60)


def _run(app: Flask) -> None:
    last: dict = {}
    while True:
        try:
            tick(app, last)
        except Exception:  # keep ticking; the heartbeat is the backstop
            app.logger.exception("schedule ticker failed")
        _sleep_to_next_minute()
