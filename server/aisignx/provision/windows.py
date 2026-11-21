"""Admin page "Set up a Windows PC": makes the one-step setup script (see winscript.py) for one PC."""
import hashlib

from flask import Response, abort, flash, render_template, request, send_file, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, Optional, Regexp

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..displays import pairing
from ..displays.agent import RUN_MODES
from ..downloads.routes import downloads_dir
from ..downloads.service import latest_download
from ..extensions import db
from ..models import Download, Playlist
from . import bp, service, winscript

SECRET_TTL_S = 2 * 3600          # the script is run soon after it is made; both codes burn on first use anyway
RUN_LABELS = {"app": "Application", "autostart": "Application with auto-start", "shell": "Shell (takes over the PC)"}


class WindowsForm(FlaskForm):
    name = StringField("Name for this screen", validators=[DataRequired(), Length(max=100)],
                       render_kw={"placeholder": "Front desk PC"})
    playlist = SelectField("Playlist", coerce=int, default=0)
    kiosk_user = StringField("Windows account the player runs as", validators=[
        Optional(), Length(max=64), Regexp(r"^[A-Za-z0-9._ -]*$", message="Letters, digits, dot, dash, underscore and space only.")],
        description="Leave empty: the script lists the accounts of that PC and you pick one there. Only needed for the shell and automatic sign-in.")
    run_as = SelectField("How the player runs", default="autostart", choices=[
        SelectChoice("app", "Application: a normal program, started by hand"),
        SelectChoice("autostart", "Application with auto-start: starts when that account signs in"),
        SelectChoice("shell", "Shell: the player replaces the Windows desktop for that account")])
    server_url = StringField("Address the PC will use to reach this server", validators=[DataRequired(), Length(max=200)])
    allow_reboot = BooleanField("Allow rebooting the PC from the web page", default=True)
    allow_startup = BooleanField("Allow changing how the player runs from the web page", default=True)
    allow_update = BooleanField("Allow installing app updates from the web page", default=True)
    allow_autologin = BooleanField("Allow automatic Windows sign-in to be set from the web page (needs https)", default=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.playlist.choices = [SelectChoice(0, "None yet")] + [SelectChoice(p.id, p.name) for p in Playlist.query.order_by(Playlist.name)]


def _files() -> dict:
    """What the script needs from the Downloads page: the one signed setup .exe (player and agent inside) and the certificate."""
    return {"player": latest_download("windows-player", "exe"), "cert": latest_download("windows-agent", "cer")}


def _problem(files: dict) -> str | None:
    missing = [label for key, label in (("player", "the Windows player installer (.exe, kind Windows player app)"),
                                        ("cert", "your public signing certificate (.cer)")) if files[key] is None]
    if missing:
        return "Upload " + ", ".join(missing) + " on the Downloads page first (kind Windows agent for the certificate)."
    return None


def _link(row: Download) -> dict:
    return {"url": request_base() + url_for("provision.file", token=service.apk_token(row.id)), "sha256": row.sha256}


def request_base() -> str:
    from .routes import _base_url

    return _base_url()


@bp.route("/windows", methods=["GET", "POST"])
@admin_required
def windows():
    files = _files()
    problem = _problem(files)
    thumbprint = None
    if problem is None:
        data = (downloads_dir() / files["cert"].stored_name).read_bytes()
        thumbprint = winscript.certificate_thumbprint(data)
        if thumbprint is None:
            problem = "The certificate file on the Downloads page is not a valid .cer (DER or PEM) certificate."
    form = WindowsForm()
    if request.method == "GET":
        form.server_url.data = request_base()
    if request.method == "POST" and problem is None and form.validate_on_submit():
        base = service.clean_base_url(form.server_url.data)
        if base is None:
            flash("Enter the server address like http://192.168.1.50:5000 or https://signs.example.com", "danger")
        else:
            http = base.startswith("http://")
            display, secret = pairing.preapprove(form.name.data, form.playlist.data or None, SECRET_TTL_S)
            display.pending_run_as = form.run_as.data if form.run_as.data in RUN_MODES else "autostart"
            display.agent_allow_reboot = bool(form.allow_reboot.data)
            display.agent_allow_startup = bool(form.allow_startup.data)
            display.agent_allow_update = bool(form.allow_update.data)
            display.agent_watchdog = display.agent_allow_restart = display.agent_allow_logs = True
            display.agent_auto_approve = True      # making this script is the admin's approval: the agent on that PC is connected when it asks
            try:
                text = winscript.build(
                    server=base, name=form.name.data, run_text=RUN_LABELS.get(form.run_as.data, "Application"),
                    kiosk_user=form.kiosk_user.data.strip(),
                    player=_link(files["player"]),
                    cert=_link(files["cert"]), thumbprint=thumbprint, pair_secret=secret,
                    allow_reboot=display.agent_allow_reboot, allow_startup=display.agent_allow_startup,
                    allow_update=display.agent_allow_update, allow_autologin=bool(form.allow_autologin.data) and not http)
            except ValueError:
                db.session.rollback()
                flash("Could not make the script from those values. Check the account name and the address.", "danger")
                text = None
            if text is not None:
                if bool(form.allow_autologin.data) and http:
                    flash("Automatic sign-in was left off: it is refused over plain http.", "warning")
                audit.record("display.provision", display, detail={"kind": "windows", "run_as": display.pending_run_as,
                                                                    "kiosk_user": form.kiosk_user.data.strip()})
                db.session.commit()
                resp = Response(text, mimetype="text/plain; charset=utf-8")
                resp.headers["Content-Disposition"] = f'attachment; filename="{winscript.SCRIPT_NAME}"'
                resp.headers["Cache-Control"] = "no-store"
                resp.headers["X-Content-Type-Options"] = "nosniff"
                return resp
    return render_template("provision/windows.html", form=form, files=files, problem=problem, thumbprint=thumbprint)


@bp.route("/file/<token>")
def file(token: str):
    """A file from the Downloads page for a device that has no account yet (the setup scripts): signed, time-limited."""
    data = service.read_token(token)
    if data is None:
        abort(404)
    row = db.session.get(Download, data["d"])
    path = downloads_dir() / row.stored_name if row else None
    if row is None or not path.is_file():
        abort(404)
    resp = send_file(path, mimetype="application/octet-stream", as_attachment=True, download_name=row.filename, max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
