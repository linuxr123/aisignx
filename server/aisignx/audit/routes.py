"""Audit log viewer (admin): filter, page through, export as CSV, set the retention."""
import json
from datetime import date, datetime, time, timedelta, timezone

import sqlalchemy as sa
from flask import flash, redirect, render_template, request, url_for

from ..auth.decorators import admin_required
from ..csvutil import stream_csv
from ..extensions import db
from ..models import AuditLog, Setting, User
from ..timeutil import iso_z, org_tz, to_local
from . import bp, service
from .forms import TARGET_RE, AuditFilterForm, RetentionForm

PER_PAGE = 50
MAX_PAGE = 10_000
CSV_BATCH = 1000

# History links and older rows may use either name for the same kind of target.
TARGET_ALIASES = {"group": ("group", "displaygroup"), "displaygroup": ("group", "displaygroup")}


def _local_midnight_utc(day: date, tz) -> datetime:
    """Start of `day` in the organization time zone, as an aware UTC datetime."""
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(timezone.utc)


def _conditions(form: AuditFilterForm) -> list:
    """SQL conditions for the valid filters in the form (invalid ones are ignored)."""
    conds = []
    user = (form.user.data or "").strip()
    if user and not form.user.errors:
        conds.append(sa.func.lower(AuditLog.username) == user.lower())
    action = form.action.data or ""
    if action in service.ACTIONS:
        if action.endswith("."):
            conds.append(AuditLog.action.startswith(action, autoescape=True))
        else:
            conds.append(AuditLog.action == action)
    target = (form.target.data or "").strip().lower()
    match = TARGET_RE.match(target) if target and not form.target.errors else None
    if match:
        ttype, tid = match.groups()
        conds.append(AuditLog.target_type.in_(TARGET_ALIASES.get(ttype, (ttype,))))
        if tid is not None:
            conds.append(AuditLog.target_id == int(tid))
    tz = org_tz()
    if form.date_from.data and not form.date_from.errors:
        conds.append(AuditLog.ts >= _local_midnight_utc(form.date_from.data, tz))
    if form.date_to.data and not form.date_to.errors:  # inclusive: before the next day
        conds.append(AuditLog.ts < _local_midnight_utc(form.date_to.data + timedelta(days=1),
                                                       tz))
    return conds


def _filter_form() -> AuditFilterForm:
    form = AuditFilterForm(request.args)
    form.validate()  # fills .errors; the page still renders with the valid filters
    return form


def _select(form: AuditFilterForm):
    return (sa.select(AuditLog).where(*_conditions(form))
            .order_by(AuditLog.ts.desc(), AuditLog.id.desc()))


def pretty_detail(row: AuditLog) -> str | None:
    data = row.detail_data
    if data is None:
        return row.detail or None
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


def _filter_args() -> dict:
    """The current filters, for pagination and export links."""
    keys = ("user", "action", "target", "date_from", "date_to")
    return {k: request.args[k] for k in keys if request.args.get(k)}


@bp.route("/")
@admin_required
def index():
    form = _filter_form()
    page = min(max(request.args.get("page", 1, type=int) or 1, 1), MAX_PAGE)
    rows = db.session.execute(
        _select(form).offset((page - 1) * PER_PAGE).limit(PER_PAGE + 1)).scalars().all()
    has_next = len(rows) > PER_PAGE
    rows = rows[:PER_PAGE]
    retention = RetentionForm(formdata=None)
    retention.retention_days.data = Setting.get_int("audit.retention_days", lo=0, hi=3650)
    usernames = db.session.execute(sa.select(User.username).order_by(User.username)).scalars()
    return render_template(
        "audit/index.html", form=form, rows=[(r, pretty_detail(r)) for r in rows], page=page,
        has_next=has_next, args=_filter_args(), retention_form=retention,
        usernames=list(usernames), action_label=service.action_label, tz_name=org_tz().key)


@bp.route("/export.csv")
@admin_required
def export():
    form = _filter_form()
    tz = org_tz()
    query = _select(form).execution_options(yield_per=CSV_BATCH)

    def rows():
        for r in db.session.execute(query).scalars():
            yield (iso_z(r.ts), to_local(r.ts, tz).strftime("%Y-%m-%d %H:%M:%S"), r.username,
                   r.via, r.token_id, r.action, r.target_type, r.target_id, r.target_name,
                   r.ip, r.detail)

    header = ("time_utc", f"time_local ({tz.key})", "user", "via", "token_id", "action",
              "target_type", "target_id", "target_name", "ip", "detail")
    stamp = to_local(datetime.now(timezone.utc), tz).strftime("%Y%m%d-%H%M")
    return stream_csv(f"aisignx-audit-{stamp}.csv", header, rows())


@bp.route("/settings", methods=["POST"])
@admin_required
def settings():
    form = RetentionForm()
    if not form.validate_on_submit():
        flash("Retention not saved: " + "; ".join(
            m for errors in form.errors.values() for m in errors), "danger")
        return redirect(url_for("audit.index"))
    old = Setting.get_int("audit.retention_days", lo=0, hi=3650)
    new = form.retention_days.data
    if old != new:
        Setting.set("audit.retention_days", new)
        service.record("audit.settings", detail={"retention_days": [old, new]})
        db.session.commit()
    flash("Keeping audit entries forever." if new == 0
          else f"Keeping audit entries for {new} days.", "success")
    return redirect(url_for("audit.index"))
