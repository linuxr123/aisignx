"""Backup pages (admin): list, create, download, upload, restore, delete, settings."""
import os

from flask import abort, flash, jsonify, redirect, render_template, send_file, session, url_for
from flask_login import current_user, logout_user

from .. import dbinfo
from ..audit import service as audit
from ..auth.decorators import admin_required
from ..extensions import db
from ..models import Setting
from ..timeutil import iso_z
from . import bp, restore, service
from .forms import ActionForm, BackupSettingsForm, CreateForm, RestoreForm, UploadForm

RESTORED_MESSAGE = ("Restore finished; sign in again. Restart the server to apply the "
                    "restored secret key.")
_SEEN_KEY = "backup_restore_seen"


def _actor():
    return (current_user.id, current_user.username)


def _redirect():
    return redirect(url_for("backup.index"), 303)


@bp.route("/")
@admin_required
def index():
    backups = service.list_backups()
    settings_form = BackupSettingsForm(data={
        "enabled": Setting.get_bool("backup.enabled"),
        "hour": Setting.get_int("backup.hour", lo=0, hi=23),
        "keep": Setting.get_int("backup.keep", lo=1, hi=100),
        "include_uploads": Setting.get_bool("backup.include_uploads"),
    })
    return render_template(
        "backup/index.html", backups=backups, total=sum(b["size"] for b in backups),
        known=dbinfo.known_revisions(), current_revision=dbinfo.current_revision(),
        stale=service.is_stale(backups), state=service.status(),
        last_ok_at=Setting.get_datetime("backup.last_ok_at"),
        last_error=Setting.get("backup.last_error") or None,
        last_error_at=Setting.get_datetime("backup.last_error_at"),
        last_scheduled_date=Setting.get("backup.last_scheduled_date"),
        backup_dir=service.backup_dir(),
        create_form=CreateForm(data={"include_uploads": Setting.get_bool("backup.include_uploads")}),
        upload_form=UploadForm(), action_form=ActionForm(), settings_form=settings_form)


@bp.route("/create", methods=["POST"])
@admin_required
def create():
    form = CreateForm()
    if not form.validate_on_submit():
        abort(400)
    include = bool(form.include_uploads.data)
    actor = _actor()

    def run():
        path = service.create("manual", include_uploads=include)
        service.audit_as("backup.create", actor, "web", target=("backup", None, path.name),
                         detail={"name": path.name, "include_uploads": include})
        return f"Backup {path.name} created."

    try:
        service.start_in_background("create", run)
        flash("Backup started.", "info")
    except service.BackupBusy as exc:
        flash(str(exc), "warning")
    return _redirect()


@bp.route("/status")
def status():
    """Progress of the background run, polled by the page. Exempt from maintenance mode.
    After a successful restore it tells a signed-in browser once that it was signed out
    (the restore invalidates every session, so current_user is anonymous by then)."""
    state = service.status()
    if state["op"] == "restore" and not state["running"] and state["ok"]:
        had_session = current_user.is_authenticated or session.get("_user_id") is not None
        if session.get(_SEEN_KEY) != state["id"] and had_session:
            logout_user()
            session[_SEEN_KEY] = state["id"]
            return jsonify(running=False, op="restore", ok=True, signed_out=True,
                           message=RESTORED_MESSAGE)
    if not current_user.is_authenticated:
        return jsonify(error="unauthorized", message="Sign in again."), 401
    if not current_user.has_role("admin"):
        abort(403)
    return jsonify(id=state["id"], running=state["running"], op=state["op"],
                   started_at=iso_z(state["started_at"]), finished_at=iso_z(state["finished_at"]),
                   ok=state["ok"], message=state["message"], signed_out=False)


@bp.route("/download/<name>")
@admin_required
def download(name):
    try:
        path = service.resolve_name(name)
    except FileNotFoundError:
        abort(404)
    audit.record_now("backup.download", ("backup", None, name))
    return send_file(path, mimetype="application/zip", as_attachment=True, download_name=name,
                     max_age=0)


@bp.route("/upload", methods=["POST"])
@admin_required
def upload():
    form = UploadForm()
    if not form.validate_on_submit():
        flash("Choose a backup archive (.zip) to upload.", "danger")
        return _redirect()
    folder = service.backup_dir()
    name = service.new_name("uploaded")
    part = folder / (name + ".part")
    try:
        form.file.data.save(part)  # streams to disk in chunks
        manifest = restore.validate(part)
        os.replace(part, folder / name)
    except restore.RestoreError as exc:
        service._remove(part)
        flash(f"Upload rejected: {exc}", "danger")
        return _redirect()
    except OSError as exc:
        service._remove(part)
        flash(f"Upload failed: {exc}", "danger")
        return _redirect()
    audit.record_now("backup.upload", ("backup", None, name),
                     detail={"name": name, "filename": (form.file.data.filename or "")[:200],
                             "created_at": manifest.get("created_at")})
    flash(f"Backup uploaded as {name}.", "success")
    return _redirect()


@bp.route("/restore/<name>", methods=["GET", "POST"])
@admin_required
def restore_page(name):
    try:
        path = service.resolve_name(name)
    except FileNotFoundError:
        abort(404)
    form = RestoreForm()
    error = None
    try:
        manifest = service.read_manifest(path)
    except service.BackupError as exc:
        manifest, error = {}, f"Damaged archive: {exc}"
    revision = manifest.get("alembic_revision")
    if not error and revision and revision not in dbinfo.known_revisions():
        error = restore.NEWER_MESSAGE
    if form.validate_on_submit() and not error:
        if form.confirm.data.strip() != name:
            form.confirm.errors.append("Type the exact file name to confirm.")
        else:
            actor = _actor()

            def run():
                result = restore.restore(path, actor_via="web", actor=actor)
                skipped = result["skipped"]
                note = f" {len(skipped)} files could not be replaced." if skipped else ""
                return RESTORED_MESSAGE + note

            try:
                service.start_in_background("restore", run)
            except service.BackupBusy as exc:
                flash(str(exc), "warning")
                return _redirect()
            # Not a redirect: every other page answers 503 while the restore runs.
            return _restore_page(name, path, manifest, None, form, started=True)
    return _restore_page(name, path, manifest, error, form)


def _restore_page(name, path, manifest, error, form, started=False):
    return render_template("backup/restore.html", name=name, manifest=manifest, error=error,
                           form=form, size=path.stat().st_size, started=started,
                           state=service.status(), current_revision=dbinfo.current_revision())


@bp.route("/delete/<name>", methods=["POST"])
@admin_required
def delete(name):
    form = ActionForm()
    if not form.validate_on_submit():
        abort(400)
    try:
        service.delete(name)
    except FileNotFoundError:
        abort(404)
    except OSError as exc:
        flash(f"Could not delete {name}: {exc}", "danger")
        return _redirect()
    audit.record_now("backup.delete", ("backup", None, name))
    flash(f"Deleted {name}.", "success")
    return _redirect()


@bp.route("/settings", methods=["POST"])
@admin_required
def settings():
    form = BackupSettingsForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for error in errors:
                flash(error, "danger")
        return _redirect()
    values = {"backup.enabled": "1" if form.enabled.data else "0",
              "backup.hour": str(form.hour.data), "backup.keep": str(form.keep.data),
              "backup.include_uploads": "1" if form.include_uploads.data else "0"}
    changed = [k for k, v in values.items() if str(Setting.get(k)) != v]
    for key, value in values.items():
        Setting.set(key, value)
    if changed:
        audit.record("backup.settings", detail={"changed": changed})
    db.session.commit()
    flash("Backup settings saved.", "success")
    return _redirect()
