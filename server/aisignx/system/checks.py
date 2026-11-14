"""System checks: the programs AiSignX relies on, and the admin self-test.

`run_checks()` is cheap (browser, ffmpeg); the dashboard calls it on every load.
`run_selftest()` checks the database, folders, disk space, background threads, jobs,
backups and mail. It runs on the System page and with `flask --app wsgi check --all`.

Threads, streams and the broker live in the server process, so they can only be judged
there (`live=True`, the default inside a request). From the command line those rows say
so instead of failing; everything stored in the database (job and backup state) is
checked either way.
"""
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import click
from flask import Flask, current_app, has_request_context
from flask.cli import with_appcontext
from sqlalchemy import text

from ..media import probe, webshot

GB = 1024 ** 3
TICKER_MAX_AGE_S = 180   # the ticker passes once a minute
JOBS_LOOP_MAX_AGE_S = 120  # the jobs thread wakes every 30 s
BACKUP_MAX_AGE_S = 48 * 3600


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    purpose: str = ""
    fix: str = ""
    duration_ms: int | None = None
    info: bool = False  # informational row: never fails
    section: str = "check"  # "check", or "runtime" for facts about the installation


def run_checks() -> list[Check]:
    browser = webshot.browser_path()
    return [
        Check(
            name="Web browser (Edge, Chrome or Chromium)",
            ok=browser is not None,
            detail=browser or "not found",
            purpose="Screenshots of web pages for previews.",
            fix="Run the installer, install Microsoft Edge or Google Chrome, "
                "or set AISIGNX_BROWSER to the browser's path.",
        ),
        Check(
            name="ffmpeg and ffprobe",
            ok=probe.ffmpeg_available(),
            detail="found" if probe.ffmpeg_available() else "not found",
            purpose="Video length, resolution and thumbnails.",
            fix="Run the installer, or install ffmpeg and make sure it is on the PATH "
                "(or set AISIGNX_FFMPEG / AISIGNX_FFPROBE).",
        ),
    ]


# --- Self-test ----------------------------------------------------------------------------

def _timed(name: str, func, purpose: str = "") -> list[Check]:
    """Run func() -> Check | list[Check], timing it. An exception becomes a failed row."""
    start = time.perf_counter()
    try:
        result = func()
    except Exception as exc:  # a broken check must never break the page
        result = Check(name, False, f"{type(exc).__name__}: {exc}"[:300], purpose)
    ms = int(round((time.perf_counter() - start) * 1000))
    rows = result if isinstance(result, list) else [result]
    for row in rows:
        if row.duration_ms is None:
            row.duration_ms = ms
    return rows


def _ago(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 120:
        return f"{seconds} s ago"
    if seconds < 7200:
        return f"{seconds // 60} min ago"
    if seconds < 172800:
        return f"{seconds // 3600} h ago"
    return f"{seconds // 86400} days ago"


def _check_db() -> Check:
    from ..extensions import db

    db.session.execute(text("SELECT 1")).scalar()
    return Check("Database", True, "SELECT 1 answered.", "Everything is stored here.")


def _check_revision() -> Check:
    from .. import dbinfo

    current, head = dbinfo.current_revision(), dbinfo.head_revision()
    ok = current is not None and current == head
    return Check("Database schema", ok, f"at {current or 'none'}, newest is {head}",
                 "The database layout must match this version of AiSignX.",
                 "" if ok else "Stop the server and run: flask --app wsgi db upgrade")


def _folders() -> list[tuple[str, str]]:
    config = current_app.config
    return [("Uploads folder", config.get("UPLOAD_FOLDER")),
            ("Backups folder", config.get("BACKUP_DIR")),
            ("Logs folder", config.get("LOG_DIR"))]


def _probe_writable(path: Path) -> None:
    """Create the folder if needed, then write and delete a temporary file in it."""
    path.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".aisignx-selftest-", dir=path)
    try:
        os.write(fd, b"ok")
    finally:
        os.close(fd)
        os.unlink(name)


def _check_folder(label: str, raw: str | None) -> Check:
    if not raw:
        return Check(label, False, "not configured", "Files are written here.")
    path = Path(raw)
    try:
        _probe_writable(path)
    except OSError as exc:
        return Check(label, False, f"{path}: not writable ({exc.strerror or exc})",
                     "Files are written here.",
                     "Check that the folder exists and that the account running "
                     "AiSignX may write to it.")
    return Check(label, True, f"{path}: writable", "Files are written here.")


def _existing(path: Path) -> Path | None:
    path = Path(path).resolve()
    for candidate in (path, *path.parents):
        if candidate.exists():
            return candidate
    return None


def _check_disks() -> list[Check]:
    from .. import dbinfo
    from ..models import Setting

    min_gb = Setting.get_int("alerts.disk_min_free_gb", lo=0, hi=100000) or 0
    min_pct = Setting.get_int("alerts.disk_min_free_pct", lo=0, hi=99) or 0
    places = [(label.split()[0].lower(), raw) for label, raw in _folders()]
    db_file = dbinfo.sqlite_path()
    if db_file is not None:
        places.insert(0, ("database", str(db_file.parent)))
    rows, seen = [], set()
    for label, raw in places:
        path = _existing(Path(raw)) if raw else None
        if path is None:
            continue
        dev = os.stat(path).st_dev
        if dev in seen:
            continue
        seen.add(dev)
        usage = shutil.disk_usage(path)
        pct = 100.0 * usage.free / usage.total if usage.total else 100.0
        ok = usage.free >= min_gb * GB and pct >= min_pct
        rows.append(Check(
            f"Free disk space ({label})", ok,
            f"{usage.free / GB:.1f} GB free of {usage.total / GB:.1f} GB ({pct:.0f}%) on {path.anchor or path}",
            "Uploads, backups and the database need room to grow.",
            "" if ok else f"Free some space; the limit is {min_gb} GB or {min_pct}% "
                          "(Alerts > Settings)."))
    return rows or [Check("Free disk space", False, "no data folder found")]


def _check_ticker(live: bool) -> Check:
    name, purpose = "Schedule ticker", "Pushes schedule changes to screens on the minute."
    app = current_app
    if not app.config.get("PLAYER_SCHEDULE_TICKER"):
        return Check(name, True, "turned off (AISIGNX_SCHEDULE_TICKER=0); screens pick up "
                     "changes with their heartbeat", purpose, info=True)
    if not live:
        return Check(name, True, "runs in the server process; see the System page",
                     purpose, info=True)
    thread = app.extensions.get("aisignx_schedule_ticker")
    if thread is None:
        return Check(name, True, "not started yet (no screen connected)", purpose)
    if not thread.is_alive():
        return Check(name, False, "the thread has stopped", purpose,
                     "Restart the server; the log file says why it stopped.")
    last = app.extensions.get("aisignx_ticker_last_tick")
    if last is None:
        return Check(name, True, "running, first pass pending", purpose)
    age = time.time() - last
    ok = age <= TICKER_MAX_AGE_S
    return Check(name, ok, f"running, last pass {_ago(age)}", purpose,
                 "" if ok else "The ticker is failing; see the log file.")


def _check_jobs_thread(live: bool) -> Check:
    from .. import jobs

    name, purpose = "Background jobs", "Alerts, scheduled backups, retention and reports."
    app = current_app
    if not app.config.get("JOBS_ENABLED"):
        return Check(name, False, "turned off (AISIGNX_JOBS=0)", purpose,
                     "Remove AISIGNX_JOBS=0 so backups, alerts and clean-up run.")
    if not live:
        return Check(name, True, "runs in the server process; see the System page",
                     purpose, info=True)
    thread = app.extensions.get(jobs.THREAD_KEY)
    if thread is None or not thread.is_alive():
        return Check(name, False, "the jobs thread is not running", purpose,
                     "Restart the server; the log file says why it stopped.")
    age = jobs.loop_age_s(app)
    if age is None:
        return Check(name, True, "running, first pass pending", purpose)
    ok = age < JOBS_LOOP_MAX_AGE_S
    return Check(name, ok, f"running, last wake-up {_ago(age)}", purpose,
                 "" if ok else "A job is hanging; see the log file and restart the server.")


def _check_each_job() -> list[Check]:
    from .. import jobs
    from ..timeutil import utcnow

    now = utcnow()
    rows = []
    for st in jobs.status():
        parts = []
        if st["last_run_at"] is None:
            parts.append("has not run yet")
        else:
            parts.append(f"last run {_ago((now - st['last_run_at']).total_seconds())}")
        if st["last_ok_at"] is not None:
            parts.append(f"last success {_ago((now - st['last_ok_at']).total_seconds())}")
        ok = not st["fail_count"]
        if not ok:
            parts.append(f"failed {st['fail_count']} time(s) in a row: {st['last_error'] or '?'}")
        rows.append(Check(f"Job {st['name']}", ok, "; ".join(parts),
                          f"Runs every {_every(st['every_s'])}.",
                          "" if ok else "See the log file for the full error."))
    return rows


def _every(seconds: int) -> str:
    if seconds % 86400 == 0:
        return f"{seconds // 86400} day(s)"
    if seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def _check_streams(live: bool) -> Check:
    from ..player.broker import get_broker

    name, purpose = "Live-update streams", "Open connections from screens."
    limit = current_app.config.get("PLAYER_MAX_STREAMS")
    if not live:
        return Check(name, True, f"limit {limit}; counted by the server process",
                     purpose, info=True)
    broker = get_broker()
    if broker is None:
        return Check(name, True, f"none yet (limit {limit})", purpose)
    count, limit = broker.count(), broker.max_streams
    ok = count < limit
    return Check(name, ok, f"{count} of {limit} open", purpose,
                 "" if ok else "Raise AISIGNX_MAX_STREAMS and restart the server.")


def _check_backup() -> Check:
    from ..models import Setting
    from ..timeutil import utcnow

    name, purpose = "Last backup", "A recent backup lets you recover from mistakes."
    last = Setting.get_datetime("backup.last_ok_at")
    enabled = Setting.get_bool("backup.enabled")
    hint = ("Create a backup in Admin > Backups." if enabled else
            "Scheduled backups are off; turn them on in Admin > Backups.")
    if last is None:
        return Check(name, False, "no backup yet", purpose, hint)
    age = (utcnow() - last).total_seconds()
    ok = age <= BACKUP_MAX_AGE_S
    detail = f"{_ago(age)} ({Setting.get('backup.last_name') or 'unnamed'})"
    error = Setting.get("backup.last_error")
    if error:
        detail += f"; last error: {error[:200]}"
    return Check(name, ok, detail, purpose, "" if ok else hint)


def _check_smtp(include_smtp: bool) -> Check:
    from ..models import Setting

    name, purpose = "E-mail (SMTP)", "Sends alert e-mails."
    host = (Setting.get("smtp.host") or "").strip()
    email_on = Setting.get_bool("alerts.email_enabled")
    if not host:
        if email_on:
            return Check(name, False, "e-mail alerts are on but no SMTP server is set",
                         purpose, "Set the SMTP server in Alerts > Settings.")
        return Check(name, True, "not configured (e-mail alerts are off)", purpose, info=True)
    if not include_smtp:
        return Check(name, True, f"configured ({host}); run the self-test to connect",
                     purpose, info=True)
    try:
        from ..alerts.mailer import check_connection
    except ImportError as exc:
        return Check(name, False, f"mail module unavailable: {exc}", purpose)
    ok, message = check_connection()
    return Check(name, bool(ok), message, purpose,
                 "" if ok else "Check the SMTP settings and AISIGNX_SMTP_PASSWORD.")


def run_selftest(include_smtp: bool = False, live: bool | None = None) -> list[Check]:
    """Every health check, timed. Needs an app context. `live` defaults to "inside a
    request", i.e. this is the server process that runs the threads."""
    if live is None:
        live = has_request_context()
    out: list[Check] = []
    out += _timed("Database", _check_db)
    out += _timed("Database schema", _check_revision)
    for label, raw in _folders():
        out += _timed(label, lambda label=label, raw=raw: _check_folder(label, raw))
    out += _timed("Free disk space", _check_disks)
    out += _timed("Schedule ticker", lambda: _check_ticker(live))
    out += _timed("Background jobs", lambda: _check_jobs_thread(live))
    out += _timed("Jobs", _check_each_job)
    out += _timed("Live-update streams", lambda: _check_streams(live))
    out += _timed("Last backup", _check_backup)
    out += _timed("E-mail (SMTP)", lambda: _check_smtp(include_smtp))
    out += _timed("Programs", run_checks)
    out += [Check(k, True, v, info=True, duration_ms=0, section="runtime")
            for k, v in runtime_info()]
    return out


# --- Runtime information ----------------------------------------------------------------

def _read(path: str) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None


def cpu_count() -> int | None:
    """CPUs available to this process, honouring a cgroup v2 quota (containers)."""
    count = len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else os.cpu_count()
    quota = _read("/sys/fs/cgroup/cpu.max")
    if quota and count:
        q, _, period = quota.partition(" ")
        if q.isdigit() and period.isdigit() and int(period):
            count = max(1, min(count, -(-int(q) // int(period))))
    return count


def memory_bytes() -> int | None:
    """Total RAM (or the cgroup v2 memory limit if lower). Best effort; None if unknown."""
    total = None
    if sys.platform == "win32":
        try:
            import ctypes

            class _Status(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = _Status()
            status.dwLength = ctypes.sizeof(_Status)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                total = int(status.ullTotalPhys)
        except Exception:
            total = None
    else:
        try:
            total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (AttributeError, ValueError, OSError):
            total = None
    limit = _read("/sys/fs/cgroup/memory.max")
    if limit and limit.isdigit():
        total = min(total, int(limit)) if total else int(limit)
    return total


def runtime_info() -> list[tuple[str, str]]:
    """(label, value) facts about this installation. Needs an app context."""
    from .. import __version__, dbinfo

    config = current_app.config
    mem = memory_bytes()
    db_file = dbinfo.sqlite_path()
    return [
        ("AiSignX version", __version__),
        ("Python", f"{platform.python_version()} ({platform.system()} {platform.release()})"),
        ("Database", dbinfo.dialect() + (f" ({db_file})" if db_file else "")),
        ("Instance folder", current_app.instance_path),
        ("Uploads folder", str(config.get("UPLOAD_FOLDER"))),
        ("Backups folder", str(config.get("BACKUP_DIR"))),
        ("Logs folder", str(config.get("LOG_DIR"))),
        ("CPUs", str(cpu_count() or "unknown")),
        ("Memory", f"{mem / GB:.1f} GB" if mem else "unknown"),
    ]


# --- CLI ----------------------------------------------------------------------------------

@click.command("check")
@click.option("--all", "run_all", is_flag=True,
              help="Also run the full self-test (database, folders, disk, jobs, backups).")
@click.option("--json", "as_json", is_flag=True, help="Print the results as JSON.")
@click.option("--smtp", is_flag=True, help="With --all: also connect to the mail server.")
@with_appcontext
def check_command(run_all: bool, as_json: bool, smtp: bool):
    """Check that required programs are installed (--all: run the self-test).

    Exits with status 1 if anything fails.
    """
    checks = run_selftest(include_smtp=smtp, live=False) if run_all else run_checks()
    failed = [c for c in checks if not c.ok and not c.info]
    if as_json:
        click.echo(json.dumps({"ok": not failed, "checks": [asdict(c) for c in checks]},
                              indent=2))
    else:
        for c in checks:
            if c.info:
                mark = click.style("INFO   ", fg="cyan")
            elif c.ok:
                mark = click.style("OK     ", fg="green")
            else:
                mark = click.style("MISSING" if not run_all else "FAILED ", fg="red")
            click.echo(f"[{mark}] {c.name}: {c.detail}")
            if not c.ok and not c.info:
                if c.purpose:
                    click.echo(f"          Needed for: {c.purpose}")
                if c.fix:
                    click.echo(f"          Fix: {c.fix}")
    if failed:
        raise SystemExit(1)


def register(app: Flask) -> None:
    app.cli.add_command(check_command)
