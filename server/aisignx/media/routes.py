from flask import abort, flash, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required

from ..audit import service as audit
from ..auth.decorators import editor_required
from ..extensions import db
from ..models import MEDIA_TYPES, Media
from ..models.media import MEDIA_IMAGE, MEDIA_PLUGIN, MEDIA_VIDEO, MEDIA_WEB, THUMB_PENDING
from . import bp, service, storage, thumbnails, webshot
from .forms import DeleteForm, MediaEditForm, ReplaceForm, UploadForm, WebPageForm
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
        browser_ok=webshot.browser_path() is not None,
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

    added, errors, heavy = 0, [], []
    for file_storage in files:
        try:
            media = service.create_from_upload(file_storage, current_user)
            audit.record("media.upload", media)
            added += 1
            if media.playback_warnings:
                heavy.append(media)
        except InvalidMediaError as exc:
            errors.append(str(exc))
    db.session.commit()
    if added:
        flash(f"Uploaded {added} file{'s' if added != 1 else ''}.", "success")
    for media in heavy:
        flash(f"{media.name}: may not play smoothly on every screen. "
              + " ".join(media.playback_warnings), "warning")
    for message in errors:
        flash(message, "danger")
    return redirect(url_for("media.index"))


@bp.route("/web", methods=["POST"])
@editor_required
def add_web():
    form = WebPageForm()
    if form.validate_on_submit():
        media = service.create_web_page(form.name.data, form.url.data, form.duration.data,
                                        current_user)
        audit.record("media.create_web", media, detail={"url": media.url})
        db.session.commit()
        thumbnails.queue_thumbnail(media)
        flash("Web page added. Its preview will appear in a few seconds.", "success")
    else:
        for field_errors in form.errors.values():
            for message in field_errors:
                flash(message, "danger")
    return redirect(url_for("media.index"))


@bp.route("/<int:media_id>/edit", methods=["GET", "POST"])
@editor_required
def edit(media_id: int):
    media = _get_media_or_404(media_id)
    if media.media_type == MEDIA_PLUGIN:  # plugin items have their own form
        return redirect(url_for("plugins.edit_item", media_id=media.id))
    form = MediaEditForm(obj=media)
    is_web = media.media_type == MEDIA_WEB
    if form.validate_on_submit():
        if is_web and not form.url.data:
            form.url.errors.append("Address is required for web pages.")
        else:
            url_changed = is_web and media.url != form.url.data.strip()
            before = {"name": media.name, "default_duration": media.default_duration,
                      "url": media.url}
            media.name = form.name.data.strip()
            media.default_duration = form.default_duration.data
            if is_web:
                media.url = form.url.data.strip()
            changed = {k: [v, getattr(media, k)] for k, v in before.items()
                       if getattr(media, k) != v}
            if changed:
                audit.record("media.update", media, detail={"changed": changed})
            db.session.commit()
            if url_changed:
                thumbnails.queue_thumbnail(media)
            flash("Media updated.", "success")
            return redirect(url_for("media.index"))
    usage = sorted({item.playlist.name for item in media.playlist_items})
    return render_template("media/edit.html", form=form, media=media, usage=usage,
                           delete_form=DeleteForm(), replace_form=ReplaceForm())


@bp.route("/<int:media_id>/replace", methods=["POST"])
@editor_required
def replace(media_id: int):
    form = ReplaceForm()
    if not form.validate_on_submit():
        abort(400)
    media = _get_media_or_404(media_id)
    if media.media_type not in (MEDIA_IMAGE, MEDIA_VIDEO):
        flash("Only uploaded files can be replaced.", "danger")
        return redirect(url_for("media.edit", media_id=media.id))
    file_storage = form.file.data
    if not file_storage or not file_storage.filename:
        flash("Choose a file to upload.", "warning")
        return redirect(url_for("media.edit", media_id=media.id))
    old_checksum = media.checksum_sha256
    try:
        old = service.replace_file(media, file_storage)
    except (InvalidMediaError, service.NotReplaceableError) as exc:
        db.session.rollback()
        flash(str(exc), "danger")
        return redirect(url_for("media.edit", media_id=media.id))
    audit.record("media.replace", media, detail={"old_checksum": old_checksum,
                                                 "filename": media.original_filename})
    service.commit_replace(media, old)
    flash("File replaced. Screens using it will update shortly.", "success")
    return redirect(url_for("media.edit", media_id=media.id))


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


@bp.route("/<int:media_id>/thumbnail/refresh", methods=["POST"])
@editor_required
def refresh_thumbnail(media_id: int):
    if not DeleteForm().validate_on_submit():
        abort(400)
    media = _get_media_or_404(media_id)
    thumbnails.queue_thumbnail(media)
    flash("Refreshing the preview. It will update in a few seconds.", "info")
    return redirect(url_for("media.edit", media_id=media.id))


@bp.route("/thumbnail-status")
@login_required
def thumbnail_status():
    """Which of the given media ids still have a pending thumbnail (used for live refresh)."""
    ids = request.args.getlist("id", type=int)[:200]
    pending = [m.id for m in Media.query.filter(Media.id.in_(ids),
                                                Media.thumbnail_status == THUMB_PENDING)]
    return {"pending": pending}


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
    # Thumbnails can be regenerated under a new name; the URL carries ?v=<name> for caching.
    return send_from_directory(storage.thumbs_dir(), media.thumbnail_name,
                               mimetype="image/jpeg", max_age=86400)
