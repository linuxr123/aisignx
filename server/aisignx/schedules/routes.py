"""Schedules admin pages. Writes go through the ORM and commit; the WATCHED commit hook
then tells the screens to fetch their manifest."""
from datetime import timedelta

from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from ..auth.decorators import editor_required
from ..displays.forms import ActionForm
from ..extensions import db
from ..models import Display, Schedule
from ..targets import applies, label
from . import bp, engine
from .forms import ScheduleForm
from .resolve import (SOURCE_SCHEDULE, is_playable_playlist, load_context, resolve,
                      week_segments)

PREVIEW_DAYS = 7


def _get_schedule_or_404(schedule_id: int) -> Schedule:
    schedule = db.session.get(Schedule, schedule_id)
    if schedule is None:
        abort(404)
    return schedule


def _require_action_form() -> None:
    if not ActionForm().validate_on_submit():
        abort(400)


def _status(schedules, ctx) -> tuple[dict, dict]:
    """({schedule id: screens playing it now}, {schedule id: badge}) from one Context.

    Badges: "empty" (playlist has nothing playable), "no_screens" (targets nobody),
    "shadowed" (occurs in the next 7 days but never wins on any targeted display).
    """
    displays = Display.query.all()
    playing: dict[int, int] = {}
    for d in displays:
        if not d.is_paired:
            continue
        res = resolve(d, ctx.now, ctx)
        if res.source["type"] == SOURCE_SCHEDULE:
            playing[res.source["id"]] = playing.get(res.source["id"], 0) + 1

    winners: set[int] = set()
    for d in displays:
        for seg in week_segments(d, ctx.now, PREVIEW_DAYS, ctx):
            if seg.source["type"] == SOURCE_SCHEDULE:
                winners.add(seg.source["id"])

    end = ctx.now + timedelta(days=PREVIEW_DAYS)
    badges: dict[int, str] = {}
    for s in schedules:
        if not s.enabled or s.id in winners:
            continue
        if not is_playable_playlist(s.playlist):
            badges[s.id] = "empty"
        elif not any(applies(s, d) for d in displays):
            badges[s.id] = "no_screens"
        elif (engine.is_active(s, ctx.now, ctx.tz)
              or engine.boundaries([s], ctx.now, end, ctx.tz)):
            badges[s.id] = "shadowed"
    return playing, badges


@bp.route("/")
@login_required
def index():
    schedules = Schedule.query.order_by(Schedule.priority.desc(), Schedule.name,
                                        Schedule.id).all()
    ctx = load_context()
    playing, badges = _status(schedules, ctx)
    return render_template("schedules/index.html", schedules=schedules, playing=playing,
                           badges=badges, labels={s.id: label(s) for s in schedules},
                           tz_name=ctx.tz.key, action_form=ActionForm())


def _edit_page(form, schedule):
    return render_template("schedules/form.html", form=form, schedule=schedule,
                           tz_name=load_context().tz.key)


@bp.route("/new", methods=["GET", "POST"])
@editor_required
def create():
    form = ScheduleForm()
    if request.method == "GET":
        if request.args.get("target"):
            form.target.data = request.args["target"]
        if request.args.get("playlist", type=int):
            form.playlist.data = request.args.get("playlist", type=int)
    if form.validate_on_submit():
        schedule = Schedule()
        form.apply(schedule)
        db.session.add(schedule)
        db.session.commit()
        current_app.logger.info("schedule %s (%s) created by user %s",
                                schedule.id, schedule.name, current_user.id)
        flash(f"Schedule {schedule.name} created.", "success")
        return redirect(url_for("schedules.index"))
    return _edit_page(form, None)


@bp.route("/<int:schedule_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(schedule_id: int):
    schedule = _get_schedule_or_404(schedule_id)
    form = ScheduleForm()
    if request.method == "GET":
        form.load(schedule)
    if form.validate_on_submit():
        form.apply(schedule)
        db.session.commit()
        flash(f"Schedule {schedule.name} saved.", "success")
        return redirect(url_for("schedules.index"))
    return _edit_page(form, schedule)


@bp.route("/<int:schedule_id>/toggle", methods=["POST"])
@editor_required
def toggle(schedule_id: int):
    _require_action_form()
    schedule = _get_schedule_or_404(schedule_id)
    schedule.enabled = not schedule.enabled
    db.session.commit()
    state = "enabled" if schedule.enabled else "disabled"
    flash(f"Schedule {schedule.name} {state}.", "success")
    return redirect(url_for("schedules.index"))


@bp.route("/<int:schedule_id>/duplicate", methods=["POST"])
@editor_required
def duplicate(schedule_id: int):
    _require_action_form()
    src = _get_schedule_or_404(schedule_id)
    copy = Schedule(
        name=f"{src.name} (copy)"[:100], playlist_id=src.playlist_id, scope=src.scope,
        display_id=src.display_id, group_id=src.group_id, days_mask=src.days_mask,
        start_time=src.start_time, end_time=src.end_time, start_date=src.start_date,
        end_date=src.end_date, priority=src.priority,
        enabled=False,  # a copy plays nothing until it is edited and enabled
    )
    db.session.add(copy)
    db.session.commit()
    flash("Schedule duplicated (disabled). Adjust it, then enable it.", "success")
    return redirect(url_for("schedules.edit", schedule_id=copy.id))


@bp.route("/<int:schedule_id>/delete", methods=["POST"])
@editor_required
def delete(schedule_id: int):
    _require_action_form()
    schedule = _get_schedule_or_404(schedule_id)
    name = schedule.name
    db.session.delete(schedule)
    db.session.commit()
    current_app.logger.info("schedule %s (%s) deleted by user %s",
                            schedule_id, name, current_user.id)
    flash(f"Deleted schedule {name}.", "success")
    return redirect(url_for("schedules.index"))
