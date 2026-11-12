from flask import abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import login_required

from ..auth.decorators import editor_required
from ..extensions import db
from ..models import EmergencyMessage, Media, Playlist, PlaylistItem, Schedule
from . import bp, service
from .forms import ActionForm, ItemForm, PlaylistForm


def _get_playlist_or_404(playlist_id: int) -> Playlist:
    playlist = db.session.get(Playlist, playlist_id)
    if playlist is None:
        abort(404)
    return playlist


def _get_item_or_404(playlist: Playlist, item_id: int) -> PlaylistItem:
    item = db.session.get(PlaylistItem, item_id)
    if item is None or item.playlist_id != playlist.id:
        abort(404)
    return item


def _require_action_form() -> None:
    if not ActionForm().validate_on_submit():
        abort(400)


def _names(rows, attr: str, limit: int = 5) -> str:
    names = [getattr(r, attr) for r in rows[:limit]]
    more = len(rows) - limit
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


def _delete_blocker(playlist: Playlist) -> str | None:
    """Why the playlist can't be deleted (schedules or a live emergency use it), or None."""
    schedules = (Schedule.query.filter_by(playlist_id=playlist.id)
                 .order_by(Schedule.name).all())
    if schedules:
        return (f"Can't delete {playlist.name}: it is used by schedule(s) "
                f"{_names(schedules, 'name')}. Change or delete those schedules first.")
    live = (EmergencyMessage.query.filter(EmergencyMessage.playlist_id == playlist.id,
                                          EmergencyMessage.cleared_at.is_(None))
            .order_by(EmergencyMessage.created_at).all())
    if live:
        return (f"Can't delete {playlist.name}: live emergency message(s) "
                f"{_names(live, 'title')} use it. Clear them first.")
    return None


def _back(playlist: Playlist):
    return redirect(url_for("playlists.detail", playlist_id=playlist.id))


@bp.route("/")
@login_required
def index():
    playlists = Playlist.query.order_by(Playlist.name).all()
    return render_template("playlists/index.html", playlists=playlists, action_form=ActionForm())


@bp.route("/new", methods=["GET", "POST"])
@editor_required
def create():
    form = PlaylistForm()
    if form.validate_on_submit():
        playlist = Playlist(name=form.name.data.strip(), description=form.description.data or None,
                            default_transition=form.default_transition.data)
        db.session.add(playlist)
        db.session.commit()
        flash("Playlist created. Now add some media.", "success")
        return _back(playlist)
    return render_template("playlists/form.html", form=form, playlist=None)


@bp.route("/<int:playlist_id>")
@login_required
def detail(playlist_id: int):
    playlist = _get_playlist_or_404(playlist_id)
    library = Media.query.order_by(Media.name).all()
    return render_template("playlists/detail.html", playlist=playlist, library=library,
                           item_form=ItemForm(), action_form=ActionForm())


@bp.route("/<int:playlist_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(playlist_id: int):
    playlist = _get_playlist_or_404(playlist_id)
    form = PlaylistForm(obj=playlist)
    if form.validate_on_submit():
        playlist.name = form.name.data.strip()
        playlist.description = form.description.data or None
        playlist.default_transition = form.default_transition.data
        db.session.commit()
        flash("Playlist saved.", "success")
        return _back(playlist)
    return render_template("playlists/form.html", form=form, playlist=playlist)


@bp.route("/<int:playlist_id>/delete", methods=["POST"])
@editor_required
def delete(playlist_id: int):
    _require_action_form()
    playlist = _get_playlist_or_404(playlist_id)
    blocker = _delete_blocker(playlist)
    if blocker:
        flash(blocker, "danger")
        return _back(playlist)
    db.session.delete(playlist)
    db.session.commit()
    flash(f"Deleted playlist {playlist.name}.", "success")
    return redirect(url_for("playlists.index"))


@bp.route("/<int:playlist_id>/duplicate", methods=["POST"])
@editor_required
def duplicate(playlist_id: int):
    _require_action_form()
    copy = service.duplicate(_get_playlist_or_404(playlist_id))
    db.session.commit()
    flash("Playlist duplicated.", "success")
    return _back(copy)


@bp.route("/<int:playlist_id>/items", methods=["POST"])
@editor_required
def add_items(playlist_id: int):
    _require_action_form()
    playlist = _get_playlist_or_404(playlist_id)
    ids = request.form.getlist("media_ids", type=int)
    added = service.add_media(playlist, ids)
    db.session.commit()
    if added:
        flash(f"Added {added} item{'s' if added != 1 else ''}.", "success")
    else:
        flash("Select media to add.", "warning")
    return _back(playlist)


@bp.route("/<int:playlist_id>/items/<int:item_id>", methods=["POST"])
@editor_required
def update_item(playlist_id: int, item_id: int):
    playlist = _get_playlist_or_404(playlist_id)
    item = _get_item_or_404(playlist, item_id)
    form = ItemForm()
    if form.validate_on_submit():
        item.duration = form.duration.data or None
        item.transition = form.transition.data or None
        item.fit_mode = form.fit_mode.data
        if item.media.is_video:  # the mute box is only shown for videos
            item.mute = form.mute.data
        service.touch(playlist)
        db.session.commit()
        flash(f"Updated {item.media.name}.", "success")
    else:
        flash("Could not save item: " + "; ".join(
            m for errors in form.errors.values() for m in errors), "danger")
    return _back(playlist)


@bp.route("/<int:playlist_id>/items/<int:item_id>/delete", methods=["POST"])
@editor_required
def remove_item(playlist_id: int, item_id: int):
    _require_action_form()
    playlist = _get_playlist_or_404(playlist_id)
    service.remove_item(playlist, _get_item_or_404(playlist, item_id))
    db.session.commit()
    return _back(playlist)


@bp.route("/<int:playlist_id>/items/<int:item_id>/move", methods=["POST"])
@editor_required
def move_item(playlist_id: int, item_id: int):
    _require_action_form()
    playlist = _get_playlist_or_404(playlist_id)
    offset = -1 if request.form.get("direction") == "up" else 1
    service.move_item(playlist, _get_item_or_404(playlist, item_id), offset)
    db.session.commit()
    return _back(playlist)


@bp.route("/<int:playlist_id>/reorder", methods=["POST"])
@editor_required
def reorder(playlist_id: int):
    """JSON endpoint for drag-and-drop: {"item_ids": [3, 1, 2]}. CSRF via X-CSRFToken header."""
    playlist = _get_playlist_or_404(playlist_id)
    data = request.get_json(silent=True) or {}
    ids = data.get("item_ids")
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify(error="item_ids must be a list of integers"), 400
    if not service.reorder(playlist, ids):
        return jsonify(error="item_ids must list every item in this playlist exactly once"), 400
    db.session.commit()
    return jsonify(ok=True, total_duration=playlist.total_duration)
