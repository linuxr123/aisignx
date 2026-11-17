import secrets
from io import BytesIO

from flask import (abort, current_app, flash, jsonify, redirect, render_template, request,
                   send_file, url_for)
from flask_login import current_user, login_required
from sqlalchemy import func

from ..audit import service as audit
from ..auth.decorators import admin_required, editor_required
from ..extensions import db
from ..emergency.service import clear_for_target
from ..models import DeviceCommand, Display, Media, Schedule
from ..player.broker import get_broker
from ..player.manifest import build_manifest
from ..pop import service as pop_service
from ..schedules.resolve import load_context, resolve, split_by_day, week_segments
from ..downloads.service import app_update, fleet_updates
from . import agent, bp, options, pairing, screenshots, service
from .forms import ActionForm, DisplayForm, PairForm, UnlockForm


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
                           fleet_updates=fleet_updates(displays) if current_user.is_admin else [],
                           states=service.states(displays), labels=service.STATE_LABELS,
                           live={d.id: service.is_live(d.id) for d in displays},
                           now_playing_map={d.id: resolve(d, ctx.now, ctx) for d in displays},
                           schedule_counts=_schedule_counts(), action_form=ActionForm(),
                           support_counts={d.id: len(options.support_problems(options.parse_diag(d.diagnostics)))
                                           for d in displays})


def _support_rows(diag: dict | None) -> list[dict]:
    """The videos a player says it cannot decode smoothly, with their names and facts."""
    problems = options.support_problems(diag)
    if not problems:
        return []
    names = {m.id: m for m in Media.query.filter(Media.id.in_([p["id"] for p in problems])).all()}
    return [{"media": names.get(p["id"]), "id": p["id"], "state": p["s"]} for p in problems]


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
            audit.record("display.pair", display,
                         detail={"replaced": replace is not None, "playlist_id": playlist_id})
            db.session.commit()
            if replace is not None:
                _close_streams(display.id)
            current_app.logger.info("display %s (%s) paired by user %s",
                                    display.id, display.name, current_user.id)
            flash("Paired. The screen will start playing in a few seconds.", "success")
            return _detail(display)
        db.session.rollback()
        already = pairing.recently_approved(form.code.data)
        if already is not None:  # e.g. the form was submitted twice
            flash(f"That code was already paired as {already.name}.", "info")
            return _detail(already)
        flash("That code is not valid or has expired.", "danger")
    return render_template("displays/pair.html", form=form)


@bp.route("/<int:display_id>")
@login_required
def detail(display_id: int):
    display = _get_display_or_404(display_id)
    ctx = load_context()
    version = build_manifest(display, ctx.now, ctx)["version"] if display.is_paired else None
    week = split_by_day(week_segments(display, ctx.now, 7, ctx), ctx.tz)
    diag = options.parse_diag(display.diagnostics)
    return render_template("displays/detail.html", display=display,
                           state=service.states([display])[display.id],
                           labels=service.STATE_LABELS, live=service.is_live(display.id),
                           manifest_version=version, res=resolve(display, ctx.now, ctx),
                           week=week, tz_name=ctx.tz.key,
                           schedule_count=_schedule_counts().get(display.id, 0),
                           current_item=service.current_item_name(display),
                           awaiting=(not display.is_paired
                                     and pairing.awaiting_claim(display)),
                           diag=diag, support=_support_rows(diag),
                           app_update=app_update(diag.get("shell") if isinstance(diag, dict) else None),
                           agent_caps=agent.capabilities(display),
                           agent_cmds=DeviceCommand.query.filter_by(display_id=display.id)
                           .order_by(DeviceCommand.id.desc()).limit(5).all(),
                           pin_set=bool(display.unlock_pin_hash),
                           screenshot_pending=bool(screenshots.pending(display)),
                           has_screenshot=screenshots.exists(display.id),
                           action_form=ActionForm(), unlock_form=UnlockForm())


@bp.route("/<int:display_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(display_id: int):
    display = _get_display_or_404(display_id)
    # not obj=: "playlist"/"group" would read the relationships
    form = DisplayForm(admin=current_user.is_admin, has_pin=bool(display.unlock_pin_hash))
    if request.method == "GET":
        _prefill(form, display)
    if form.validate_on_submit():
        before = _snapshot(display)
        display.name = form.name.data.strip()
        display.playlist_id = form.playlist.data or None
        display.group_id = form.group.data or None
        display.location = (form.location.data or "").strip() or None
        display.description = (form.description.data or "").strip() or None
        display.orientation = form.orientation.data
        display.fit_override = form.fit_override.data or None
        display.volume = form.volume.data
        display.show_offline_banner = bool(form.show_offline_banner.data)
        pin_changed = False
        if form.is_admin_form:
            display.allow_input = bool(form.allow_input.data)
            display.kiosk_lock = bool(form.kiosk_lock.data)
            display.agent_watchdog = bool(form.agent_watchdog.data)
            display.agent_allow_restart = bool(form.agent_allow_restart.data)
            display.agent_allow_logs = bool(form.agent_allow_logs.data)
            display.agent_allow_reboot = bool(form.agent_allow_reboot.data)
            display.agent_allow_startup = bool(form.agent_allow_startup.data)
            display.agent_allow_update = bool(form.agent_allow_update.data)
            if form.resume_after_min.data is not None:
                display.resume_after_s = form.resume_after_min.data * 60
            if form.clear_pin.data:
                pin_changed = display.unlock_pin_hash is not None
                options.clear_unlock_pin(display)
            elif form.unlock_pin.data:
                options.set_unlock_pin(display, form.unlock_pin.data)
                pin_changed = True
        changed = [k for k, v in _snapshot(display).items() if before[k] != v]
        if pin_changed:
            changed.append("unlock_pin")  # that it changed, never the PIN
        if changed:
            audit.record("display.update", display, detail={"changed": changed})
        db.session.commit()  # an ORM write: the screen gets the new settings at once
        if pin_changed:  # never the PIN itself
            current_app.logger.info("unlock PIN changed for display %s by user %s",
                                    display.id, current_user.id)
        flash("Display saved.", "success")
        return _detail(display)
    return render_template("displays/form.html", form=form, display=display,
                           pin_set=bool(display.unlock_pin_hash))


_AUDITED_FIELDS = ("name", "playlist_id", "group_id", "location", "description", "orientation",
                   "fit_override", "volume", "show_offline_banner", "allow_input",
                   "kiosk_lock", "resume_after_s", "agent_watchdog", "agent_allow_restart",
                   "agent_allow_logs",
                   "agent_allow_reboot", "agent_allow_startup", "agent_allow_update")


def _snapshot(display: Display) -> dict:
    return {f: getattr(display, f) for f in _AUDITED_FIELDS}


def _prefill(form: DisplayForm, display: Display) -> None:
    form.name.data = display.name
    form.playlist.data = display.playlist_id or 0
    form.group.data = display.group_id or 0
    form.location.data = display.location
    form.description.data = display.description
    form.orientation.data = display.orientation
    form.fit_override.data = display.fit_override or ""
    form.volume.data = display.volume
    form.show_offline_banner.data = display.show_offline_banner
    if form.is_admin_form:
        form.allow_input.data = display.allow_input  # the PIN is never prefilled
        form.kiosk_lock.data = display.kiosk_lock
        form.agent_watchdog.data = display.agent_watchdog
        form.agent_allow_restart.data = display.agent_allow_restart
        form.agent_allow_logs.data = display.agent_allow_logs
        form.agent_allow_reboot.data = display.agent_allow_reboot
        form.agent_allow_startup.data = display.agent_allow_startup
        form.agent_allow_update.data = display.agent_allow_update
        form.resume_after_min.data = max(1, (display.resume_after_s or 300) // 60)


@bp.route("/<int:display_id>/reload", methods=["POST"])
@editor_required
def reload(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    broker = get_broker()
    sent = broker.publish(display.id, "command", {"action": "reload"}) if broker else 0
    audit.record_now("display.reload", display, detail={"delivered": bool(sent)})
    if sent:
        flash("Reload sent.", "success")
    else:
        flash("The display is not connected right now.", "warning")
    return _back(display)


def _publish(display: Display, data: dict, force: bool = False) -> int:
    broker = get_broker()
    return broker.publish(display.id, "command", data, force=force) if broker else 0


@bp.route("/<int:display_id>/agent/<action>", methods=["POST"])
@admin_required
def agent_command(display_id: int, action: str):
    """Ask the display's device agent to do something (docs/device-agent-design.md). Admin only,
    only when switched on for this display and the device says it can; stored, then the screen is
    told to look. Never a free-form command: `action` must be one of a closed list."""
    _require_action_form()
    display = _get_display_or_404(display_id)
    params = None
    if action == "set_startup":  # the only command that takes choices from the admin
        params = {}
        if request.form.get("autostart") in ("on", "off"):
            params["autostart"] = request.form["autostart"] == "on"
        if request.form.get("mode") in ("app", "shell"):
            params["mode"] = request.form["mode"]
    cmd, reason = agent.create(display, action, current_user, params=params)
    if cmd is None:
        flash(reason, "warning")
        return _back(display)
    audit.record("display.agent_command", display,
                 detail={"command": action, "request_id": cmd.request_id, **({"params": cmd.params} if cmd.params else {})})
    db.session.commit()
    delivered = _publish(display, {"action": "commands"}, force=True)  # only a wake-up
    flash(f"{agent.LABELS[action]}: sent to the screen." if delivered else
          f"{agent.LABELS[action]}: the screen is not connected right now; it will pick this up when it checks in "
          f"(within {agent.TTL_S[action] // 60} min).", "success" if delivered else "info")
    return _back(display)


@bp.route("/<int:display_id>/logs/<request_id>")
@admin_required
def agent_logs(display_id: int, request_id: str):
    """Download a log the device uploaded (admins only)."""
    display = _get_display_or_404(display_id)
    path = agent.log_path(display.id, request_id)
    if path is None or not path.is_file():
        abort(404)
    audit.record_now("display.agent_result", display, detail={"command": "download_logs", "request_id": request_id})
    return send_file(path, mimetype="application/gzip", as_attachment=True,
                     download_name=f"display-{display.id}-{request_id}.log.gz", max_age=0)


@bp.route("/<int:display_id>/identify", methods=["POST"])
@editor_required
def identify(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    sent = _publish(display, {"action": "identify", "seconds": 15})
    audit.record_now("display.identify", display, detail={"delivered": bool(sent)})
    if sent:
        flash("The screen shows its name for 15 seconds.", "success")
    else:
        flash("The display is not connected right now.", "warning")
    return _back(display)


@bp.route("/<int:display_id>/screenshot", methods=["POST"])
@editor_required
def screenshot(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    if not display.is_paired:
        flash("This display is not paired.", "warning")
        return _back(display)
    rid = screenshots.request(display)
    if rid is None:
        flash("A screenshot was just requested.", "info")
        return _back(display)
    audit.record("display.screenshot_request", display)
    db.session.commit()
    if _publish(display, {"action": "screenshot", "request_id": rid}, force=True):
        flash("Screenshot requested. It appears here in a few seconds.", "success")
    else:
        flash("Not connected; the request stays open for 2 minutes.", "warning")
    return _back(display)


@bp.route("/<int:display_id>/screenshot.jpg")
@login_required
def screenshot_image(display_id: int):
    display = _get_display_or_404(display_id)
    try:
        # Read into memory (a few hundred KB): an open file handle would block the
        # next upload's os.replace, and deleting the display, on Windows.
        data = screenshots.path_for(display.id).read_bytes()
    except FileNotFoundError:
        abort(404)
    resp = send_file(BytesIO(data), mimetype="image/jpeg", max_age=0)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/<int:display_id>/diagnostics/refresh", methods=["POST"])
@editor_required
def refresh_diagnostics(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    if _publish(display, {"action": "diagnostics"}):
        flash("Asked the screen for fresh diagnostics. Reload in a few seconds.", "success")
    else:
        flash("The display is not connected right now.", "warning")
    return _back(display)


def _lock_command(display: Display, data: dict, event: str, done: str):
    """Send a live unlock/lock command (never stored; a reconnect does not repeat it)."""
    if not display.is_paired:
        flash("This display is not paired.", "warning")
        return _back(display)
    sent = _publish(display, {**data, "request_id": secrets.token_hex(8)}, force=True)
    detail = {"delivered": bool(sent)}
    if "minutes" in data:
        detail["minutes"] = data["minutes"]
    audit.record_now(event, display, detail=detail)
    if sent:
        flash(done, "success")
    else:
        flash("The display is not connected right now.", "warning")
    return _back(display)


@bp.route("/<int:display_id>/unlock", methods=["POST"])
@admin_required
def unlock(display_id: int):
    form = UnlockForm()
    if not form.validate_on_submit():
        abort(400)
    display = _get_display_or_404(display_id)
    minutes = form.minutes.data
    return _lock_command(display, {"action": "unlock", "minutes": minutes},
                         "display.unlock", f"Unlocked for {minutes} minutes.")


@bp.route("/<int:display_id>/relock", methods=["POST"])
@admin_required
def relock(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    return _lock_command(display, {"action": "lock"}, "display.relock", "Locked again.")


@bp.route("/<int:display_id>/unpair", methods=["POST"])
@editor_required
def unpair(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    service.unpair(display)
    # ORM writes are fine here: unpairing changes the display row and pushes anyway.
    display.screenshot_request = None
    display.screenshot_requested_at = None
    audit.record("display.unpair", display)
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
    pop_service.keep_plays_of(ident)  # before the cascade removes its raw play events
    cleared = clear_for_target(display=display)  # keeps the history (reason target_deleted)
    audit.record("display.delete", display,
                 detail={"schedules_removed": _schedule_counts().get(ident, 0),
                         "emergencies_cleared": len(cleared)})
    db.session.delete(display)  # the DB cascade removes its display-scoped schedules
    db.session.commit()
    _close_streams(ident)
    screenshots.remove(ident)
    current_app.logger.info("display %s (%s) deleted by user %s", ident, name, current_user.id)
    flash(f"Deleted display {name}.", "success")
    return redirect(url_for("displays.index"))


def _back(display: Display):
    """Return to the list or the detail page, whichever the action was sent from."""
    if request.form.get("next") == "index":
        return redirect(url_for("displays.index"))
    return _detail(display)
