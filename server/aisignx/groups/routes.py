"""Display groups: list, create, edit (name, description, members) and delete.

All writes go through the ORM, so the commit hook tells screens to refetch.
"""
from flask import abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from ..audit import service as audit
from ..auth.decorators import editor_required
from ..emergency import service as emergency_service
from ..extensions import db
from ..models import DisplayGroup, Schedule
from . import bp
from .forms import ActionForm, GroupForm


def _get_group_or_404(group_id: int) -> DisplayGroup:
    group = db.session.get(DisplayGroup, group_id)
    if group is None:
        abort(404)
    return group


def _require_action_form() -> None:
    if not ActionForm().validate_on_submit():
        abort(400)


def _schedule_counts() -> dict[int, int]:
    rows = (db.session.query(Schedule.group_id, func.count(Schedule.id))
            .filter(Schedule.group_id.isnot(None)).group_by(Schedule.group_id).all())
    return dict(rows)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def _save(group: DisplayGroup, form: GroupForm) -> int:
    """Copy the form onto the group. Returns how many displays left another group."""
    group.name = form.name.data.strip()
    group.description = (form.description.data or "").strip() or None
    group.sync_playback = bool(form.sync_playback.data)
    wanted = set(form.members.data or [])
    moved = 0
    for display in form.displays:
        if display.id in wanted:
            if display.group_id is not None and display.group is not group:
                moved += 1
            display.group = group
        elif display.group is group:
            display.group = None
    return moved


def _target(group: DisplayGroup) -> tuple:
    """Audit target for a group: type "group" (matches the History link)."""
    if group.id is None:
        db.session.flush()
    return ("group", group.id, group.name)


def _saved(group: DisplayGroup, moved: int, verb: str):
    action = "group.create" if verb == "created" else "group.update"
    audit.record(action, _target(group),
                 detail={"members": len(group.displays), "moved_from_other_groups": moved,
                         "sync_playback": group.sync_playback})
    db.session.commit()
    current_app.logger.info("group %s (%s) %s by user %s", group.id, group.name, verb,
                            current_user.id)
    message = f"Group {group.name} {verb}."
    if moved:
        message += f" Moved {_plural(moved, 'display')} from other groups."
    flash(message, "success")
    return redirect(url_for("groups.index"))


@bp.route("/")
@login_required
def index():
    groups = DisplayGroup.query.order_by(DisplayGroup.name).all()
    return render_template("groups/index.html", groups=groups,
                           schedule_counts=_schedule_counts(), action_form=ActionForm())


@bp.route("/new", methods=["GET", "POST"])
@editor_required
def create():
    form = GroupForm()
    if form.validate_on_submit():
        group = DisplayGroup()
        db.session.add(group)
        moved = _save(group, form)
        return _saved(group, moved, "created")
    return render_template("groups/form.html", form=form, group=None)


@bp.route("/<int:group_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(group_id: int):
    group = _get_group_or_404(group_id)
    form = GroupForm(obj=group, group=group)
    if request.method == "GET":
        form.members.data = [d.id for d in group.displays]
    if form.validate_on_submit():
        moved = _save(group, form)
        return _saved(group, moved, "saved")
    return render_template("groups/form.html", form=form, group=group)


@bp.route("/<int:group_id>/delete", methods=["POST"])
@editor_required
def delete(group_id: int):
    _require_action_form()
    group = _get_group_or_404(group_id)
    name, ident = group.name, group.id
    schedules = Schedule.query.filter(Schedule.group_id == ident).count()
    # Keep emergency history: clear the group's live messages instead of orphaning them.
    # Members stay; the database sets their group_id to NULL and removes the schedules.
    cleared = emergency_service.clear_for_target(group=group)
    audit.record("group.delete", _target(group),
                 detail={"members": len(group.displays), "schedules_removed": schedules,
                         "emergencies_cleared": len(cleared)})
    for display in list(group.displays):
        display.group = None
    db.session.delete(group)
    db.session.commit()
    current_app.logger.info("group %s (%s) deleted by user %s (%d schedules removed, "
                            "%d emergencies cleared)", ident, name, current_user.id,
                            schedules, len(cleared))
    message = f"Deleted group {name}. Its displays were kept"
    message += f"; {_plural(schedules, 'schedule')} removed." if schedules else "."
    flash(message, "success")
    return redirect(url_for("groups.index"))
