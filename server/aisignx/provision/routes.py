"""Provisioning page (admin) and the time-limited link the tablet downloads the app from."""
from flask import abort, current_app, flash, redirect, render_template, request, send_file, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import BooleanField, PasswordField, SelectField, StringField
from wtforms.fields.choices import SelectChoice
from wtforms.validators import DataRequired, Length, Optional

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..displays import pairing
from ..downloads.routes import downloads_dir
from ..downloads.service import latest_download
from ..extensions import db
from ..models import Download, Playlist
from ..timeutil import org_tz
from . import bp, service

SECRET_TTL_S = 24 * 3600


class ProvisionForm(FlaskForm):
    name = StringField("Name for this screen", validators=[DataRequired(), Length(max=100)],
                       render_kw={"placeholder": "Lobby tablet"})
    playlist = SelectField("Playlist", coerce=int, default=0)
    server_url = StringField("Address the tablet will use to reach this server", validators=[DataRequired(), Length(max=200)])
    lock = BooleanField("Lock the tablet down as a kiosk (recommended)", default=True)
    manage = BooleanField("Allow restart, reboot, log collection and app updates from the web page", default=True)
    wifi_ssid = StringField("Wi-Fi name (optional)", validators=[Optional(), Length(max=64)])
    wifi_security = SelectField("Wi-Fi security", choices=[SelectChoice("WPA", "WPA / WPA2 / WPA3"),
                                                          SelectChoice("WEP", "WEP"), SelectChoice("NONE", "Open (no password)")])
    wifi_password = PasswordField("Wi-Fi password", validators=[Optional(), Length(max=64)],
                                  render_kw={"autocomplete": "new-password"})

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.playlist.choices = [SelectChoice(0, "None yet")] + [SelectChoice(p.id, p.name)
                                                                  for p in Playlist.query.order_by(Playlist.name)]


def _installer():
    return latest_download("android-player")


@bp.route("/android", methods=["GET", "POST"])
@admin_required
def android():
    download = _installer()
    package = service.detect_package(downloads_dir() / download.stored_name) if download else None
    form = ProvisionForm()
    if request.method == "GET":
        host = request.host_url.rstrip("/")
        local = request.host.split(":")[0] in ("localhost", "127.0.0.1", "::1", "[::1]")
        form.server_url.data = service.lan_address(request.host.rsplit(":", 1)[-1] if ":" in request.host else 80
                                                    ) if local else host
    problem = None
    if download is None:
        problem = "Upload the Android app (an APK) on the Downloads page first, as kind \"Android player app\"."
    elif package is None:
        problem = "This does not look like an AiSignX Android app (its package is not com.aisignx.player)."
    if request.method == "POST" and not problem and form.validate_on_submit():
        base = service.clean_base_url(form.server_url.data)
        if base is None:
            flash("Enter the server address like http://192.168.1.50:5000", "danger")
            return render_template("provision/android.html", form=form, download=download, package=package,
                                   problem=problem, result=None), 400
        display, secret = pairing.preapprove(form.name.data, form.playlist.data or None, SECRET_TTL_S)
        display.kiosk_lock = bool(form.lock.data)
        if form.manage.data:
            display.agent_watchdog = display.agent_allow_restart = display.agent_allow_logs = True
            display.agent_allow_reboot = display.agent_allow_startup = display.agent_allow_update = True
        wifi = {"ssid": form.wifi_ssid.data, "security": form.wifi_security.data,
                "password": form.wifi_password.data} if form.wifi_ssid.data else None
        payload = service.build_payload(base_url=base, download=download, package=package, pairing_secret=secret,
                                        wifi=wifi, timezone=org_tz().key)
        audit.record("display.provision", display, detail={"package": package, "lock": bool(form.lock.data),
                                                           "managed": bool(form.manage.data)})
        db.session.commit()
        result = {"svg": service.qr_svg(payload), "display": display, "base": base,
                  "http": base.startswith("http://")}
        current_app.logger.info("provisioning QR made for display %s by user %s", display.id, current_user.id)
        return render_template("provision/android.html", form=form, download=download, package=package,
                               problem=None, result=result)
    return render_template("provision/android.html", form=form, download=download, package=package,
                           problem=problem, result=None)


@bp.route("/apk/<token>")
def apk(token: str):
    """The app, for the device that is being provisioned. No login (the tablet has no account yet): the link is
    unguessable and expires, and the device checks the file against the checksum in the QR code anyway."""
    data = service.read_token(token)
    if data is None:
        abort(404)
    row = db.session.get(Download, data["d"])
    path = downloads_dir() / row.stored_name if row else None
    if row is None or row.sha256 != data.get("h") or not path.is_file():
        abort(404)
    resp = send_file(path, mimetype="application/vnd.android.package-archive", as_attachment=True,
                     download_name="aisignx.apk", max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
