"""System pages (admin): health checks, the self-test and the log viewer."""
from flask import (Response, abort, current_app, flash, redirect, render_template, request,
                   url_for)
from flask_wtf import FlaskForm

from ..auth.decorators import admin_required
from ..logging_setup import log_file_path
from . import bp
from .checks import run_selftest
from .logs import LEVELS, level_of, tail

LINE_CHOICES = (200, 500, 1000)
MAX_DOWNLOAD = 64 * 1024 * 1024


class SelfTestForm(FlaskForm):
    """CSRF only."""


def _render(include_smtp: bool):
    checks = run_selftest(include_smtp=include_smtp)
    rows = [c for c in checks if c.section != "runtime"]
    info = [c for c in checks if c.section == "runtime"]
    failed = sum(1 for c in rows if not c.ok and not c.info)
    return render_template("system/index.html", checks=rows, info=info, failed=failed,
                           ran_selftest=include_smtp, form=SelfTestForm())


@bp.route("/")
@admin_required
def index():
    return _render(include_smtp=False)


@bp.route("/selftest", methods=["POST"])
@admin_required
def selftest():
    if not SelfTestForm().validate_on_submit():
        abort(400)
    return _render(include_smtp=True)


@bp.route("/logs")
@admin_required
def logs():
    lines = request.args.get("lines", type=int)
    if lines not in LINE_CHOICES:
        lines = LINE_CHOICES[0]
    level = (request.args.get("level") or "").strip().upper()
    if level not in LEVELS:
        level = ""
    auto = request.args.get("auto") == "1"
    path = log_file_path(current_app)
    try:
        entries = [(line, level_of(line)) for line in tail(path, lines, level or None)]
    except OSError as exc:
        entries = []
        flash(f"Cannot read the log file: {exc}", "danger")
    return render_template("system/logs.html", entries=entries, lines=lines, level=level,
                           auto=auto, path=path, exists=path.is_file(),
                           line_choices=LINE_CHOICES, levels=LEVELS)


@bp.route("/logs/download")
@admin_required
def logs_download():
    path = log_file_path(current_app)
    try:
        # Read into memory (at most 5 MB normally) rather than streaming from disk, so the
        # file is not held open: on Windows that would block the log rotation.
        with open(path, "rb") as fh:
            data = fh.read(MAX_DOWNLOAD)
    except OSError:
        flash("There is no log file yet.", "warning")
        return redirect(url_for("system.logs"))
    return Response(data, mimetype="text/plain; charset=utf-8", headers={
        "Content-Disposition": 'attachment; filename="aisignx.log"',
        "Cache-Control": "no-store"})
