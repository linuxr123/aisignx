"""
Background job runner - Phase 1, Task 7.

A small in-process job system. Two flavors:

  1. submit(fn, *args, **kwargs)     - one-shot work; runs ASAP on a worker
  2. schedule_periodic(fn, every_s)  - recurring work; first run after every_s

Each job runs inside the Flask app context so it can use the ORM, settings,
storage, etc. Jobs that raise are logged and retried on the next tick (for
periodic) or dropped (for one-shot). The runner is daemonic -- it exits with
the process; no graceful shutdown story is needed in Phase 1.

This is intentionally NOT Celery / RQ / dramatiq:
    * Phase 1 single-process server only
    * No external broker (no Redis dependency)
    * Sub-second latency not required
    * Crash recovery comes from the periodic schedule itself

Phase 2+ may swap in a real broker by reimplementing this module's three
public functions (start, submit, schedule_periodic) with the same signature.

Usage
-----
    from jobs import submit, schedule_periodic

    submit(send_welcome_email, user.id)
    schedule_periodic(flush_heartbeats, every_s=60, name='heartbeat-flush')
"""
import threading
import time
import queue
from dataclasses import dataclass

from logging_config import logger


_app = None              # Flask app object (captured by start())
_workers = []
_scheduler_thread = None
_periodic = []
_queue: 'queue.Queue' = queue.Queue()
_started = False
_lock = threading.Lock()


@dataclass
class _Periodic:
    fn:       any
    every_s:  float
    name:     str
    next_run: float


# -----------------------------------------------------------------------------
# Worker loop
# -----------------------------------------------------------------------------
def _worker_loop(worker_id: int):
    """Drains the one-shot queue. Each task is a (fn, args, kwargs) tuple."""
    while True:
        try:
            task = _queue.get(timeout=1.0)
        except queue.Empty:
            continue
        if task is None:
            return
        fn, args, kwargs = task
        try:
            with _app.app_context():
                fn(*args, **kwargs)
        except Exception as e:
            logger.exception(f'jobs[worker {worker_id}]: {fn.__name__} failed: {e}')
        finally:
            _queue.task_done()


def _scheduler_loop():
    """Single thread that fires periodic jobs at their next_run time. Each
    fired job is enqueued onto the regular worker queue so periodic and
    one-shot work share the same pool."""
    while True:
        now = time.time()
        next_wakeup = now + 5.0
        for p in _periodic:
            if p.next_run <= now:
                _queue.put((p.fn, (), {}))
                p.next_run = now + p.every_s
                logger.debug(f'jobs[scheduler]: enqueued periodic {p.name!r}')
            if p.next_run < next_wakeup:
                next_wakeup = p.next_run
        time.sleep(max(0.5, min(5.0, next_wakeup - time.time())))


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def start(app, worker_count: int = 2):
    """Spin up worker + scheduler threads. Idempotent."""
    global _app, _started, _scheduler_thread
    with _lock:
        if _started:
            return
        _app = app
        for i in range(max(1, worker_count)):
            t = threading.Thread(target=_worker_loop, args=(i,),
                                 daemon=True, name=f'jobs-worker-{i}')
            t.start()
            _workers.append(t)
        sched = threading.Thread(target=_scheduler_loop, daemon=True,
                                 name='jobs-scheduler')
        sched.start()
        _scheduler_thread = sched
        _started = True
        logger.info(f'jobs: started {worker_count} worker(s) + scheduler.')


def submit(fn, *args, **kwargs):
    """Enqueue a one-shot job. Returns immediately; the job runs on a worker
    thread. Raises if the runner hasn't been started."""
    if not _started:
        raise RuntimeError('jobs.start() must be called before submit()')
    _queue.put((fn, args, kwargs))


def schedule_periodic(fn, every_s: float, name: str = None,
                      first_run_delay: float = None):
    """Register a function to run every `every_s` seconds. The first run
    happens after `first_run_delay` seconds (default = every_s, so the job
    doesn't fire immediately at boot)."""
    name = name or fn.__name__
    if first_run_delay is None:
        first_run_delay = every_s
    _periodic.append(_Periodic(
        fn=fn,
        every_s=float(every_s),
        name=name,
        next_run=time.time() + float(first_run_delay),
    ))
    logger.info(f'jobs: scheduled periodic {name!r} every {every_s}s.')


def queue_size() -> int:
    """Approximate # of pending one-shot jobs. For monitoring / debug."""
    return _queue.qsize()


def periodic_jobs():
    """List currently registered periodic jobs (for monitoring)."""
    now = time.time()
    return [{'name': p.name, 'every_s': p.every_s,
             'next_run_in_s': max(0, p.next_run - now)}
            for p in _periodic]
