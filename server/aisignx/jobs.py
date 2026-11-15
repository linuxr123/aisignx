"""Background jobs: one daemon thread per process runs periodic maintenance tasks.

A job is a function `f() -> str | None` (a short status) that runs inside an app
context and raises on failure. Its state lives in Settings, so it survives restarts:

    job.<name>.last_run_at   claimed start time (ISO 8601 UTC)
    job.<name>.last_ok_at    last success
    job.<name>.last_error    last failure message ("" after a success)
    job.<name>.fail_count    failures in a row

A job is claimed with a conditional UPDATE on last_run_at before it runs, so even two
processes sharing a database never run the same job twice. The thread wakes every 30 s.
It is started by `flask --app wsgi serve` and by the first request, never by
create_app(), so `flask db upgrade` and other CLI commands start no thread.
No job starts while this process is in maintenance mode (a restore is running).
"""
import importlib
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from flask import Flask
from sqlalchemy.exc import IntegrityError

from . import maintenance
from .extensions import db
from .models import Setting
from .timeutil import as_utc, utcnow

log = logging.getLogger(__name__)

THREAD_KEY = "aisignx_jobs"
LOOP_KEY = "aisignx_jobs_last_loop"
WAKE_S = 30
MAX_ERROR = 500
_lock = threading.Lock()


@dataclass(frozen=True)
class Job:
    name: str
    every_s: int
    target: str  # "package.module:function", imported when the job runs

    def resolve(self):
        module, _, func = self.target.partition(":")
        return getattr(importlib.import_module(module), func)


JOBS = (
    Job("alerts.sweep", 60, "aisignx.alerts.rules:sweep_job"),
    Job("alerts.mail", 60, "aisignx.alerts.mailer:mail_job"),
    Job("alerts.purge", 86400, "aisignx.alerts.service:purge_job"),
    Job("backup.scheduled", 900, "aisignx.backup.service:scheduled_job"),
    Job("audit.purge", 21600, "aisignx.audit.service:purge_job"),
    Job("pop.rollup", 3600, "aisignx.pop.service:rollup_job"),
    Job("media.orphans", 86400, "aisignx.media.service:orphan_job"),
    Job("plugin_cache_prune", 86400, "aisignx.plugins.cache:prune_job"),
)


def get_job(name: str) -> Job:
    for job in JOBS:
        if job.name == name:
            return job
    raise KeyError(name)


def _key(name: str, field: str) -> str:
    return f"job.{name}.{field}"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return as_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def is_due(job: Job, last_run_at: datetime | None, now: datetime) -> bool:
    """Due when it never ran, or when every_s has passed since the last start.
    A last run in the future (clock moved back) counts as due."""
    if last_run_at is None:
        return True
    elapsed = (as_utc(now) - as_utc(last_run_at)).total_seconds()
    return elapsed >= job.every_s or elapsed < 0


def _raw(key: str) -> tuple[bool, str | None]:
    """(row exists, raw value) read straight from the database."""
    row = db.session.execute(
        sa.select(Setting.__table__.c.value).where(Setting.__table__.c.key == key)).first()
    return (row is not None, row[0] if row is not None else None)


def claim(name: str, old: str | None, now: datetime) -> bool:
    """Atomically move job.<name>.last_run_at from `old` (None: unset) to `now`.

    Commits. True only for the caller that won the claim.
    """
    table = Setting.__table__
    key = _key(name, "last_run_at")
    new = as_utc(now).isoformat()
    cond = table.c.value.is_(None) if old is None else table.c.value == old
    try:
        result = db.session.execute(
            sa.update(table).where(table.c.key == key, cond).values(value=new))
        if result.rowcount == 1:
            db.session.commit()
            return True
        if old is not None:
            db.session.rollback()
            return False
        db.session.execute(sa.insert(table).values(key=key, value=new))
        db.session.commit()
        return True
    except IntegrityError:  # another process inserted the row first
        db.session.rollback()
        return False


def _execute(job: Job) -> str | None:
    """Run a claimed job and record the outcome. Re-raises the job's exception."""
    try:
        result = job.resolve()()
    except Exception as exc:
        log.exception("Job %s failed", job.name)
        db.session.rollback()
        try:
            count = Setting.get_int(_key(job.name, "fail_count"), lo=0, default=0) or 0
            Setting.set(_key(job.name, "last_error"),
                        (f"{type(exc).__name__}: {exc}")[:MAX_ERROR])
            Setting.set(_key(job.name, "fail_count"), count + 1)
            db.session.commit()
        except Exception:
            db.session.rollback()
            log.exception("Could not record the failure of job %s", job.name)
        raise
    Setting.set_datetime(_key(job.name, "last_ok_at"), utcnow())
    Setting.set(_key(job.name, "last_error"), "")
    Setting.set(_key(job.name, "fail_count"), 0)
    db.session.commit()
    if result:
        log.info("Job %s: %s", job.name, result)
    return result


def run_now(app: Flask, name: str, force: bool = False) -> str | None:
    """Run one job now if it is due (always with force=True). Returns its status.

    The failure is recorded first, then the job's exception is re-raised.
    """
    job = get_job(name)
    with app.app_context():
        now = utcnow()
        _exists, old = _raw(_key(name, "last_run_at"))
        if force:
            Setting.set_datetime(_key(name, "last_run_at"), now)
            db.session.commit()
        elif not (is_due(job, _parse(old), now) and claim(name, old, now)):
            return None
        return _execute(job)


def run_due(app: Flask, now: datetime | None = None) -> list[str]:
    """One pass of the loop: run every due job that this process claims.

    A failing job never stops the others. Returns the names of the jobs that ran.
    Nothing runs in maintenance mode: a restore is swapping the database and uploads.
    """
    ran = []
    for job in JOBS:
        if maintenance.active() is not None:
            break
        try:
            with app.app_context():
                when = now or utcnow()
                _exists, old = _raw(_key(job.name, "last_run_at"))
                if not is_due(job, _parse(old), when) or not claim(job.name, old, when):
                    continue
                ran.append(job.name)
                _execute(job)
        except Exception:  # already logged and recorded by _execute (or a DB error)
            log.debug("Job %s raised", job.name, exc_info=True)
    return ran


def status() -> list[dict]:
    """State of every job (needs an app context)."""
    out = []
    for job in JOBS:
        out.append({
            "name": job.name,
            "every_s": job.every_s,
            "last_run_at": Setting.get_datetime(_key(job.name, "last_run_at")),
            "last_ok_at": Setting.get_datetime(_key(job.name, "last_ok_at")),
            "last_error": Setting.get(_key(job.name, "last_error")) or None,
            "fail_count": Setting.get_int(_key(job.name, "fail_count"), lo=0, default=0) or 0,
        })
    return out


def loop_age_s(app: Flask) -> float | None:
    """Seconds since the jobs thread last woke, or None if it never ran here."""
    last = app.extensions.get(LOOP_KEY)
    return None if last is None else max(0.0, time.time() - last)


def ensure_started(app: Flask) -> None:
    """Start the jobs thread unless it runs already; restarts a dead thread.

    Does nothing unless JOBS_ENABLED, and never while TESTING. Cheap when running.
    """
    if not app.config.get("JOBS_ENABLED") or app.config.get("TESTING"):
        return
    thread = app.extensions.get(THREAD_KEY)
    if thread is not None and thread.is_alive():
        return
    with _lock:
        thread = app.extensions.get(THREAD_KEY)
        if thread is not None and thread.is_alive():
            return
        thread = threading.Thread(target=_loop, args=(app,), name="aisignx-jobs", daemon=True)
        app.extensions[THREAD_KEY] = thread
        thread.start()


def _loop(app: Flask) -> None:
    while True:
        app.extensions[LOOP_KEY] = time.time()
        try:
            run_due(app)
        except Exception:  # keep looping whatever happens
            app.logger.exception("jobs loop failed")
        time.sleep(WAKE_S)
