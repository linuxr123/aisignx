"""Play reports (every signed-in user, read-only) and proof-of-play settings (admin)."""
from datetime import date, timedelta

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from ..audit import service as audit
from ..auth.decorators import admin_required, role_required
from ..csvutil import stream_csv
from ..extensions import db
from ..models import Display, DisplayGroup, Media, Setting
from ..timeutil import org_tz, to_local, utcnow
from . import bp, service
from .forms import PopSettingsForm

MAX_RANGE_DAYS = 3660


def _date(value) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _filters() -> dict:
    """Normalized report filters from the query string (bad values fall back)."""
    today = to_local(utcnow(), org_tz()).date()
    end = _date(request.args.get("end")) or today
    start = _date(request.args.get("start")) or end - timedelta(days=6)
    if start > end:
        start, end = end, start
    if (end - start).days > MAX_RANGE_DAYS:
        start = end - timedelta(days=MAX_RANGE_DAYS)

    target = (request.args.get("target") or "").strip()
    display_ids = None
    if target[:1] == "d" and target[1:].isdigit():
        display_ids = [int(target[1:])]
    elif target[:1] == "g" and target[1:].isdigit():
        display_ids = list(db.session.scalars(
            db.select(Display.id).where(Display.group_id == int(target[1:]))))
    else:
        target = ""

    media_id = request.args.get("media", type=int)
    if media_id is not None and media_id < 1:
        media_id = None
    group = request.args.get("group", "media")
    if group not in service.GROUPS:
        group = "media"
    return {"start": start, "end": end, "target": target, "display_ids": display_ids,
            "media_id": media_id, "group": group, "today": today}


def _query_args(f: dict) -> dict:
    """The filters as query-string arguments for links (CSV, settings redirect)."""
    args = {"start": f["start"].isoformat(), "end": f["end"].isoformat(), "group": f["group"]}
    if f["target"]:
        args["target"] = f["target"]
    if f["media_id"]:
        args["media"] = f["media_id"]
    return args


@bp.route("/plays")
@role_required("viewer")
def report():
    f = _filters()
    data = service.report(f["start"], f["end"], f["group"], f["display_ids"], f["media_id"])
    form = None
    if current_user.is_admin:
        form = PopSettingsForm(formdata=None, enabled=Setting.get_bool("pop.enabled"),
                               raw_days=service.raw_days(), daily_days=service.daily_days())
    return render_template(
        "pop/report.html", f=f, data=data, args=_query_args(f), form=form,
        enabled=Setting.get_bool("pop.enabled"),
        last_rollup=Setting.get_datetime("job.pop.rollup.last_ok_at"),
        raw_first=service.raw_first_day(), raw_days=service.raw_days(),
        displays=db.session.scalars(db.select(Display).order_by(Display.name)).all(),
        groups=db.session.scalars(db.select(DisplayGroup).order_by(DisplayGroup.name)).all(),
        media=db.session.execute(db.select(Media.id, Media.name).order_by(Media.name)).all(),
    )


@bp.route("/plays.csv")
@role_required("viewer")
def plays_csv():
    f = _filters()
    header = ["day", "display_id", "display", "media_id", "media", "plays", "seconds"]

    def out():  # queried while streaming: the view's session is closed by then
        for r in service.daily_rows(f["start"], f["end"], f["display_ids"], f["media_id"]):
            yield [r.day.isoformat(), r.display_id, r.display_name, r.media_id, r.media_name,
                   r.plays, r.seconds]

    return stream_csv(f"plays_{f['start']}_{f['end']}.csv", header, out())


@bp.route("/plays-raw.csv")
@role_required("viewer")
def plays_raw_csv():
    f = _filters()
    tz = org_tz()
    first = service.raw_first_day(tz=tz)
    if f["start"] < first:
        flash(f"Individual plays are kept for {service.raw_days()} days. Choose a start date "
              f"on or after {first.isoformat()} for the raw export.", "warning")
        return redirect(url_for("pop.report", **_query_args(f)))
    header = ["started_at_utc", "started_local", "display_id", "display", "media_id", "media",
              "playlist_item_id", "duration_s", "received_at_utc"]

    def out():  # queried while streaming: the view's session is closed by then
        rows = service.raw_rows(f["start"], f["end"], f["display_ids"], f["media_id"], tz=tz)
        for e, name in rows:
            yield [e.started_at, to_local(e.started_at, tz).strftime("%Y-%m-%d %H:%M:%S"),
                   e.display_id, name or f"Display #{e.display_id}", e.media_id, e.media_name,
                   e.item_id, round(e.duration_ms / 1000, 1), e.received_at]

    return stream_csv(f"plays-raw_{f['start']}_{f['end']}.csv", header, out())


@bp.route("/settings", methods=["POST"])
@admin_required
def settings():
    form = PopSettingsForm()
    if not form.validate_on_submit():
        errors = "; ".join(f"{form[name].label.text}: {', '.join(msgs)}"
                           for name, msgs in form.errors.items() if name in form)
        flash(f"Proof-of-play settings were not saved. {errors}".strip(), "danger")
        return redirect(url_for("pop.report") + "#pop-settings")
    new = {"pop.enabled": "1" if form.enabled.data else "0",
           "pop.raw_days": str(form.raw_days.data), "pop.daily_days": str(form.daily_days.data)}
    changed = {k: {"from": Setting.get(k), "to": v} for k, v in new.items() if Setting.get(k) != v}
    for key, value in new.items():
        Setting.set(key, value)
    if changed:
        audit.record("pop.settings", detail={"changed": changed})
    db.session.commit()
    flash("Proof-of-play settings saved.", "success")
    return redirect(url_for("pop.report") + "#pop-settings")
