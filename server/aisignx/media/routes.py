from flask import abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required

from ..auth.decorators import editor_required
from ..extensions import db
from ..models import MEDIA_TYPES, Media
from ..models.media import MEDIA_WEB
from . import bp, service, storage
from .forms import DeleteForm, MediaEditForm, UploadForm, WebPageForm
from .probe import InvalidMediaError, ffmpeg_available


def _get_media_or_404(media_id: int) -> Media:
    media = db.session.get(Media, media_id)
    if media is None:
        abort(404)
    return media


@bp.route("/")
@login_required
def index():
    media_type = request.args.get("type", "")
    search = (request.args.get("q") or "").strip()
    query = Media.query
    if media_type in MEDIA_TYPES:
        query = query.filter(Media.media_type == media_type)
    if search:
        query = query.filter(Media.name.ilike(f"%{search}%"))
    items = query.order_by(Media.created_at.desc(), Media.id.desc()).all()
    return render_template(
        "media/index.html",
        items=items, media_type=media_type, search=search,
        upload_form=UploadForm(), web_form=WebPageForm(), delete_form=DeleteForm(),
        default_duration=service.default_duration(), ffmpeg_ok=ffmpeg_available(),
    )


@bp.route("/upload", methods=["POST"])
@editor_required
def upload():
    form = UploadForm()
    if not form.validate_on_submit():
        abort(400)
    files = [f for f in (form.files.data or []) if f and f.filename]
    if not files:
        flash("Choose at least one file to upload.", "warning")
        return redirect(url_for("media.index"))

    added, errors = 0, []
    for file_storage in files:
        try:
            service.create_from_upload(file_storage, current_user)
            added += 1
        except InvalidMediaError as exc:
            errors.append(str(exc))
    db.session.commit()
    if added:
        flash(f"Uploaded {added} file{'s' if added != 1 else ''}.", "success")
    for message in errors:
        flash(message, "danger")
    return redirect(url_for("media.index"))


@bp.route("/web", methods=["POST"])
@editor_required
def add_web():
    form = WebPageForm()
    if form.validate_on_submit():
        service.create_web_page(form.name.data, form.url.data, form.duration.data, current_user)
        db.session.commit()
        flash("Web page added.", "success")
    else:
        for field_errors in form.errors.values():
            for message in field_errors:
                flash(message, "danger")
    return redirect(url_for("media.index"))


@bp.route("/<int:media_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(media_id: int):
    media = _get_media_or_404(media_id)
    form = MediaEditForm(obj=media)
    is_web = media.media_type == MEDIA_WEB
    if form.validate_on_submit():
        if is_web and not form.url.data:
            form.url.errors.append("Address is required for web pages.")
        else:
            media.name = form.name.data.strip()
            media.default_duration = form.default_duration.data
            if is_web:
                media.url = form.url.data.strip()
            db.session.commit()
            flash("Media updated.", "success")
            return redirect(url_for("media.index"))
    usage = sorted({item.playlist.name for item in media.playlist_items})
    return render_template("media/edit.html", form=form, media=media, usage=usage,
                           delete_form=DeleteForm())


@bp.route("/<int:media_id>/delete", methods=["POST"])
@editor_required
def delete(media_id: int):
    if not DeleteForm().validate_on_submit():
        abort(400)
    media = _get_media_or_404(media_id)
    name = media.name
    service.delete_media(media)
    flash(f"Deleted {name}.", "success")
    return redirect(url_for("media.index"))


@bp.route("/<int:media_id>/file")
@login_required
def file(media_id: int):
    media = _get_media_or_404(media_id)
    if not media.stored_name:
        abort(404)
    return send_from_directory(storage.media_dir(), media.stored_name,
                               mimetype=media.mime_type, max_age=3600)


@bp.route("/<int:media_id>/thumb")
@login_required
def thumb(media_id: int):
    media = _get_media_or_404(media_id)
    if not media.thumbnail_name:
        abort(404)
    return send_from_directory(storage.thumbs_dir(), media.thumbnail_name,
                               mimetype="image/jpeg", max_age=86400)
