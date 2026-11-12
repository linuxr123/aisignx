from flask import abort, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from ..auth.decorators import editor_required
from ..extensions import db
from ..emergency.service import clear_for_target
from ..models import Display, Schedule
from ..player.broker import get_broker
from ..player.manifest import build_manifest
from ..schedules.resolve import load_context, resolve, split_by_day, week_segments
from . import bp, pairing, service
from .forms import ActionForm, DisplayForm, PairForm


def _get_display_or_404(display_id: int) -> Display:
    display = db.session.get(Display, display_id)
    if display is None:
        abort(404)
    return display


def _require_action_form() -> None:
    if not ActionForm().validate_on_submit():
        abort(400)


def _close_streams(display_id: int) -> None:
    broker = get_broker()
    if broker is not None:
        broker.close(display_id)


def _schedule_counts() -> dict[int, int]:
    """{display id: schedules that target exactly that display} (removed with it)."""
    rows = (db.session.query(Schedule.display_id, func.count(Schedule.id))
            .filter(Schedule.display_id.isnot(None)).group_by(Schedule.display_id).all())
    return dict(rows)


def _detail(display: Display):
    return redirect(url_for("displays.detail", display_id=display.id))


@bp.route("/")
@login_required
def index():
    displays = Display.query.order_by(Display.name).all()
    ctx = load_context()
    return render_template("displays/index.html", displays=displays,
                           states=service.states(displays), labels=service.STATE_LABELS,
                           live={d.id: service.is_live(d.id) for d in displays},
                           now_playing_map={d.id: resolve(d, ctx.now, ctx) for d in displays},
                           schedule_counts=_schedule_counts(), action_form=ActionForm())


@bp.route("/status.json")
@login_required
def status():
    return jsonify(service.status_payload())


@bp.route("/pair", methods=["GET", "POST"])
@editor_required
def pair():
    form = PairForm()
    if form.validate_on_submit():
        replace = None
        if form.pair_as.data:
            replace = db.session.get(Display, form.pair_as.data)
        playlist_id = form.playlist.data or None
        display = pairing.approve(form.code.data, form.name.data, playlist_id, replace,
                                  current_user)
        if display is not None:
            db.session.commit()
            if replace is not None:
                _close_streams(display.id)
            current_app.logger.info("display %s (%s) paired by user %s",
                                    display.id, display.name, current_user.id)
            flash("Paired. The screen will start playing in a few seconds.", "success")
            return _detail(display)
        db.session.rollback()
        flash("That code is not valid or has expired.", "danger")
    return render_template("displays/pair.html", form=form)


@bp.route("/<int:display_id>")
@login_required
def detail(display_id: int):
    display = _get_display_or_404(display_id)
    ctx = load_context()
    version = build_manifest(display, ctx.now, ctx)["version"] if display.is_paired else None
    week = split_by_day(week_segments(display, ctx.now, 7, ctx), ctx.tz)
    return render_template("displays/detail.html", display=display,
                           state=service.states([display])[display.id],
                           labels=service.STATE_LABELS, live=service.is_live(display.id),
                           manifest_version=version, res=resolve(display, ctx.now, ctx),
                           week=week, tz_name=ctx.tz.key,
                           schedule_count=_schedule_counts().get(display.id, 0),
                           current_item=service.current_item_name(display),
                           action_form=ActionForm())


@bp.route("/<int:display_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(display_id: int):
    display = _get_display_or_404(display_id)
    form = DisplayForm()  # not obj=: "playlist"/"group" would read the relationships
    if request.method == "GET":
        form.name.data = display.name
        form.playlist.data = display.playlist_id or 0
        form.group.data = display.group_id or 0
    if form.validate_on_submit():
        display.name = form.name.data.strip()
        display.playlist_id = form.playlist.data or None
        display.group_id = form.group.data or None
        db.session.commit()
        flash("Display saved.", "success")
        return _detail(display)
    return render_template("displays/form.html", form=form, display=display)


@bp.route("/<int:display_id>/reload", methods=["POST"])
@editor_required
def reload(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    broker = get_broker()
    sent = broker.publish(display.id, "command", {"action": "reload"}) if broker else 0
    if sent:
        flash("Reload sent.", "success")
    else:
        flash("The display is not connected right now.", "warning")
    return _back(display)


@bp.route("/<int:display_id>/unpair", methods=["POST"])
@editor_required
def unpair(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    service.unpair(display)
    db.session.commit()
    _close_streams(display.id)
    current_app.logger.info("display %s (%s) unpaired by user %s",
                            display.id, display.name, current_user.id)
    flash(f"Unpaired {display.name}. Its screen will show a new pairing code.", "success")
    return _back(display)


@bp.route("/<int:display_id>/delete", methods=["POST"])
@editor_required
def delete(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    name, ident = display.name, display.id
    clear_for_target(display=display)  # keeps the history rows (clear_reason target_deleted)
    db.session.delete(display)  # the DB cascade removes its display-scoped schedules
    db.session.commit()
    _close_streams(ident)
    current_app.logger.info("display %s (%s) deleted by user %s", ident, name, current_user.id)
    flash(f"Deleted display {name}.", "success")
    return redirect(url_for("displays.index"))


def _back(display: Display):
    """Return to the list or the detail page, whichever the action was sent from."""
    if request.form.get("next") == "index":
        return redirect(url_for("displays.index"))
    return _detail(display)
