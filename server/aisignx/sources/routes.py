"""Admin pages for image sources. Admin only: a folder source reads the server's disk."""
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..extensions import db
from ..models import ImageSource, Media
from ..timeutil import utcnow
from . import bp, service
from .forms import HELP, KIND_LABELS, SourceForm
from .http import SourceError


class Csrf(FlaskForm):
    """Only the CSRF token."""


def _counts() -> dict:
    return dict(db.session.query(Media.source_id, db.func.count(Media.id)).filter(Media.source_id.isnot(None)).group_by(Media.source_id).all())


def _page(form=None, editing=None, status=200, preview=None):
    return render_template("sources/index.html", sources=ImageSource.query.order_by(ImageSource.name).all(), counts=_counts(), form=form,
                           editing=editing, csrf=Csrf(), help=HELP, kinds=KIND_LABELS, preview=preview), status


@bp.route("/")
@admin_required
def index():
    return _page(SourceForm())


@bp.route("/new", methods=["POST"])
@admin_required
def create():
    form = SourceForm()
    if not form.validate_on_submit():
        return _page(form, status=400)
    if ImageSource.query.filter_by(name=form.name.data.strip()).first():
        form.name.errors.append("A source with this name exists.")
        return _page(form, status=400)
    src = ImageSource()
    _apply(src, form)
    src.next_sync_at = utcnow()
    db.session.add(src)
    db.session.flush()
    audit.record("source.create", src, detail={"kind": src.kind})
    db.session.commit()
    flash(f"{src.name} added. The first sync starts within a minute.", "success")
    return redirect(url_for("sources.index"))


def _apply(src: ImageSource, form: SourceForm) -> None:
    src.name = form.name.data.strip()
    src.kind = form.kind.data
    src.location = form.location.data.strip()
    src.username = (form.username.data or "").strip()
    if form.secret.data:                       # an empty box keeps the saved one
        service.set_secret(src, form.secret.data)
    src.recursive, src.mirror, src.enabled = bool(form.recursive.data), bool(form.mirror.data), bool(form.enabled.data)
    src.interval_min, src.max_images = form.interval_min.data, form.max_images.data


def _get(source_id: int) -> ImageSource:
    return db.session.get(ImageSource, source_id) or abort(404)


@bp.route("/<int:source_id>", methods=["GET", "POST"])
@admin_required
def edit(source_id: int):
    src = _get(source_id)
    form = SourceForm(obj=src) if request.method == "GET" else SourceForm()
    if request.method == "POST":
        if not form.validate_on_submit():
            return _page(form, src, 400)
        clash = ImageSource.query.filter(ImageSource.name == form.name.data.strip(), ImageSource.id != src.id).first()
        if clash:
            form.name.errors.append("A source with this name exists.")
            return _page(form, src, 400)
        _apply(src, form)
        src.next_sync_at = utcnow()
        audit.record("source.update", src)
        db.session.commit()
        flash("Saved. It will sync again within a minute.", "success")
        return redirect(url_for("sources.index"))
    form.secret.data = ""
    return _page(form, src)


@bp.route("/<int:source_id>/test", methods=["POST"])
@admin_required
def test(source_id: int):
    if not Csrf().validate_on_submit():
        abort(400)
    src = _get(source_id)
    try:
        names = service.preview(src)
        flash(f"{src.name}: found {len(names)} entries. First ones: " + ", ".join(names[:5]), "success")
    except SourceError as exc:
        flash(f"{src.name}: {exc}", "danger")
    return redirect(url_for("sources.index"))


@bp.route("/<int:source_id>/sync", methods=["POST"])
@admin_required
def sync_now(source_id: int):
    if not Csrf().validate_on_submit():
        abort(400)
    src = _get(source_id)
    src.next_sync_at = utcnow()
    db.session.commit()
    flash(f"{src.name} will sync within a minute.", "info")
    return redirect(url_for("sources.index"))


@bp.route("/<int:source_id>/delete", methods=["POST"])
@admin_required
def delete(source_id: int):
    if not Csrf().validate_on_submit():
        abort(400)
    src = _get(source_id)
    name = src.name
    audit.record("source.delete", src, detail={"by": current_user.username})
    n = service.delete_source(src)
    flash(f"{name} removed with its {n} imported image{'s' if n != 1 else ''}.", "success")
    return redirect(url_for("sources.index"))
