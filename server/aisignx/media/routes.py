from flask import abort, current_app, flash, jsonify, redirect, render_template, request, send_from_directory, url_for
from flask_login import current_user, login_required

from ..audit import service as audit
from ..auth.decorators import editor_required
from ..extensions import db
from ..models import MEDIA_TYPES, ImageSource, Media
from ..models.media import MEDIA_IMAGE, MEDIA_PLUGIN, MEDIA_VIDEO, MEDIA_WEB, THUMB_PENDING
from . import bp, convert, service, storage, thumbnails, webpage, webshot
from .forms import ConvertForm, DeleteForm, MediaEditForm, ReplaceForm, UploadForm, WebPageForm
from .probe import InvalidMediaError, ffmpeg_available


def _get_media_or_404(media_id: int) -> Media:
    media = db.session.get(Media, media_id)
    if media is None:
        abort(404)
    return media


PER_PAGE = 96


@bp.route("/")
@login_required
def index():
    media_type = request.args.get("type", "")
    search = (request.args.get("q") or "").strip()
    query = Media.query
    # Images copied from an image source (Plugins > Image sources) stay out of the main list unless asked for: ?source=all or ?source=<id>.
    source = request.args.get("source", "")
    if source == "all":
        pass
    elif source.isdigit():
        query = query.filter(Media.source_id == int(source))
    else:
        query = query.filter(Media.source_id.is_(None))
    if media_type in MEDIA_TYPES:
        query = query.filter(Media.media_type == media_type)
    if search:
        query = query.filter(Media.name.ilike(f"%{search}%"))
    # Pages of PER_PAGE: hundreds of imported pictures would otherwise make the page slow and hard to use.
    total = query.count()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    items = query.order_by(Media.created_at.desc(), Media.id.desc()).offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()
    return render_template(
        "media/index.html",
        items=items, media_type=media_type, search=search, source=source, page=page, pages=max(1, -(-total // PER_PAGE)), total=total,
        sources=ImageSource.query.order_by(ImageSource.name).all(),
        upload_form=UploadForm(), web_form=WebPageForm(), delete_form=DeleteForm(),
        default_duration=service.default_duration(), ffmpeg_ok=ffmpeg_available(),
        max_upload_mb=current_app.config["MAX_CONTENT_LENGTH"] // (1024 * 1024),
        browser_ok=webshot.browser_path() is not None,
    )


def _wants_json() -> bool:
    """The drag-and-drop uploader (static/js/app.js) sends X-Requested-With: fetch and reads JSON."""
    return request.headers.get("X-Requested-With") == "fetch"


@bp.route("/upload", methods=["POST"])
@editor_required
def upload():
    form = UploadForm()
    if not form.validate_on_submit():
        if _wants_json():
            return jsonify(added=0, errors=["The page expired. Reload it and try again."],
                           warnings=[]), 400
        abort(400)
    files = [f for f in (form.files.data or []) if f and f.filename]
    if not files:
        if _wants_json():
            return jsonify(added=0, errors=["No file was received."], warnings=[]), 400
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
    if _wants_json():
        return jsonify(added=added, errors=errors,
                       warnings=[f"{m.name}: may not play smoothly on every screen. "
                                 + " ".join(m.playback_warnings) for m in heavy]),             (200 if added or not errors else 422)
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
                                        current_user, form.web_mode.data, form.refresh_min.data)
        audit.record("media.create_web", media, detail={"url": media.url, "mode": media.web_mode})
        db.session.commit()
        thumbnails.queue_thumbnail(media)
        webpage.queue(media)  # does the site allow embedding? take the screenshot if needed
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
                      "url": media.url, "web_mode": media.web_mode,
                      "web_refresh_s": media.web_refresh_s}
            media.name = form.name.data.strip()
            media.default_duration = form.default_duration.data
            if is_web:
                media.url = form.url.data.strip()
                media.web_mode = (form.web_mode.data if form.web_mode.data in webpage.MODES
                                  else "auto")
                media.web_refresh_s = webpage.clamp_refresh((form.refresh_min.data or 5) * 60)
            changed = {k: [v, getattr(media, k)] for k, v in before.items()
                       if getattr(media, k) != v}
            if changed:
                audit.record("media.update", media, detail={"changed": changed})
            web_changed = is_web and (url_changed or "web_mode" in changed
                                      or "web_refresh_s" in changed)
            if web_changed:
                webpage.reset(media, url_changed)
            db.session.commit()
            if url_changed:
                thumbnails.queue_thumbnail(media)
            if web_changed:
                webpage.queue(media)
            flash("Media updated.", "success")
            return redirect(url_for("media.index"))
    if is_web and not form.is_submitted():
        form.refresh_min.data = max(1, (media.web_refresh_s or 300) // 60)
    usage = sorted({item.playlist.name for item in media.playlist_items})
    return render_template("media/edit.html", form=form, media=media, usage=usage,
                           delete_form=DeleteForm(), replace_form=ReplaceForm(),
                           convert_form=ConvertForm(), can_convert=convert.available(),
                           convert=convert.status(media.id))


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


@bp.route("/<int:media_id>/webpage/refresh", methods=["POST"])
@editor_required
def refresh_webpage(media_id: int):
    """Check again whether a web page can be embedded, and take a new screenshot now."""
    if not DeleteForm().validate_on_submit():
        abort(400)
    media = _get_media_or_404(media_id)
    if media.media_type != MEDIA_WEB:
        abort(404)
    media.web_checked_at = None
    media.snapshot_at = None
    db.session.commit()
    webpage.queue(media)
    flash("Checking the page. Its status updates in a few seconds.", "info")
    return redirect(url_for("media.edit", media_id=media.id))


@bp.route("/<int:media_id>/convert", methods=["POST"])
@editor_required
def convert_video(media_id: int):
    """Make a TV-friendly copy of a video in the background (media/convert.py)."""
    form = ConvertForm()
    if not form.validate_on_submit():
        abort(400)
    media = _get_media_or_404(media_id)
    if media.media_type != MEDIA_VIDEO or not media.stored_name:
        flash("Only uploaded videos can be converted.", "danger")
    elif not convert.available():
        flash("ffmpeg was not found on the server, so videos cannot be converted.", "danger")
    elif convert.start(media, current_user, bool(form.replace.data)):
        flash("Converting. This can take a few minutes for a long video; you can leave this page.",
              "info")
    else:
        flash("This video is already being converted.", "warning")
    return redirect(url_for("media.edit", media_id=media.id))


@bp.route("/<int:media_id>/convert.json")
@login_required
def convert_status(media_id: int):
    _get_media_or_404(media_id)
    return jsonify(convert.status(media_id))


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
