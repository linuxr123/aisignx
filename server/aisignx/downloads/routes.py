"""Everyone signed in may download; admins upload and delete. Files live outside the media
folder (and so outside media backups), under a random name; the browser only ever gets the
original file name as an attachment."""
import hashlib
import os
import re
import secrets
from pathlib import Path

from flask import abort, current_app, flash, redirect, render_template, send_file, url_for
from flask_login import current_user, login_required
from sqlalchemy import update

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..extensions import db
from ..models import DOWNLOAD_KINDS, Download
from . import bp
from .forms import ActionForm, UploadForm

ALLOWED = {"exe", "msi", "zip", "apk", "vsto", "pdf", "dmg", "pkg", "deb", "rpm", "sh", "ps1", "txt", "md"}
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ ()+-]")


def downloads_dir() -> Path:
    path = Path(current_app.config["UPLOAD_FOLDER"]).parent / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _clean_name(name: str) -> str:
    name = _SAFE_NAME.sub("_", os.path.basename(name or "").replace("\\", "/").rsplit("/", 1)[-1]).strip(". ")
    return name[:160]


@bp.route("/")
@login_required
def index():
    items = Download.query.order_by(Download.created_at.desc()).all()
    groups = [(label, [d for d in items if d.kind == kind]) for kind, label in DOWNLOAD_KINDS.items()]
    return render_template("downloads/index.html", groups=[g for g in groups if g[1]],
                           upload_form=UploadForm(), action_form=ActionForm(),
                           can_manage=current_user.is_admin, total=len(items))


@bp.route("/upload", methods=["POST"])
@admin_required
def upload():
    form = UploadForm()
    if not form.validate_on_submit():
        flash("Choose a file to upload.", "danger")
        return redirect(url_for("downloads.index"))
    name = _clean_name(form.file.data.filename)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if not name or ext not in ALLOWED:
        flash("That file type is not allowed here (" + ", ".join(sorted(ALLOWED)) + ").", "danger")
        return redirect(url_for("downloads.index"))
    stored = secrets.token_hex(16)
    path = downloads_dir() / stored
    digest, size = hashlib.sha256(), 0
    try:
        with open(path, "wb") as out:
            for chunk in iter(lambda: form.file.data.stream.read(1 << 20), b""):
                digest.update(chunk)
                size += len(chunk)
                out.write(chunk)
        if size == 0:
            raise ValueError("empty")
    except Exception:
        path.unlink(missing_ok=True)
        flash("The file was empty or could not be saved.", "danger")
        return redirect(url_for("downloads.index"))
    row = Download(kind=form.kind.data, version=(form.version.data or "").strip(), filename=name,
                   stored_name=stored, size=size, sha256=digest.hexdigest(),
                   notes=(form.notes.data or "").strip(), uploaded_by=current_user.username)
    db.session.add(row)
    db.session.flush()
    audit.record("download.upload", row, detail={"file": name, "size": size, "kind": row.kind})
    db.session.commit()
    flash(f"Added {name}.", "success")
    return redirect(url_for("downloads.index"))


@bp.route("/<int:download_id>/get")
@login_required
def get(download_id: int):
    row = db.session.get(Download, download_id) or abort(404)
    path = downloads_dir() / row.stored_name
    if not path.is_file():
        abort(404)
    db.session.execute(update(Download).where(Download.id == row.id)
                       .values(download_count=Download.download_count + 1))
    db.session.commit()
    resp = send_file(path, mimetype="application/octet-stream", as_attachment=True,
                     download_name=row.filename, max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@bp.route("/<int:download_id>/delete", methods=["POST"])
@admin_required
def delete(download_id: int):
    row = db.session.get(Download, download_id) or abort(404)
    if not ActionForm().validate_on_submit():
        abort(400)
    (downloads_dir() / row.stored_name).unlink(missing_ok=True)
    audit.record("download.delete", row, detail={"file": row.filename})
    db.session.delete(row)
    db.session.commit()
    flash("Removed.", "info")
    return redirect(url_for("downloads.index"))
