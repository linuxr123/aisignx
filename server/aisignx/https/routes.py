"""The HTTPS page (admin): the certificate, the nginx configuration, and what to do on the server."""
from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_wtf import FlaskForm

from ..audit import service as audit
from ..auth.decorators import admin_required
from ..extensions import db
from . import bp, certs, nginx, service


class Csrf(FlaskForm):
    """Only the CSRF token."""


def _page(**extra):
    info = certs.current(service.tls_dir())
    state = service.connection_state()
    ctx = {"info": info, "state": state, "notices": service.notices(info, state), "form": Csrf(),
           "names": service.suggested_names(info), "fingerprint": certs.fingerprint_text(info["fingerprint"]) if info else "",
           "folder": service.tls_dir()}
    ctx.update(extra)
    return render_template("https/index.html", **ctx)


@bp.route("/")
@admin_required
def index():
    return _page()


@bp.route("/self-signed", methods=["POST"])
@admin_required
def self_signed():
    if not Csrf().validate_on_submit():
        abort(400)
    try:
        cert_pem, key_pem = certs.make_self_signed(certs.clean_names(request.form.get("names", "")))
        chain, key, _ = certs.check_pair(cert_pem, key_pem)
    except certs.CertError as e:
        flash(str(e), "danger")
        return _page(), 400
    certs.store(service.tls_dir(), chain, key)
    audit.record("https.self_signed", detail={"names": certs.describe(chain[0])["names"]})
    db.session.commit()
    flash("A self-signed certificate was made. nginx picks it up when it is reloaded (setup-nginx does that by itself).", "success")
    return redirect(url_for("https.index"))


def _upload_text(field: str) -> bytes:
    f = request.files.get(field)
    if f and f.filename:
        return f.stream.read(certs.MAX_PEM_BYTES + 1)
    return (request.form.get(field + "_text") or "").strip().encode()


@bp.route("/import", methods=["POST"])
@admin_required
def import_cert():
    if not Csrf().validate_on_submit():
        abort(400)
    try:
        chain, key, problems = certs.check_pair(_upload_text("cert"), _upload_text("key"), request.form.get("passphrase") or None)
    except certs.CertError as e:
        flash(str(e), "danger")
        return _page(), 400
    certs.store(service.tls_dir(), chain, key)
    info = certs.describe(chain[0])
    audit.record("https.import", detail={"subject": info["subject"], "names": info["names"]})
    db.session.commit()
    flash("The certificate was stored. nginx picks it up when it is reloaded (setup-nginx does that by itself).", "success")
    for p in problems:
        flash(p, "warning")
    return redirect(url_for("https.index"))


@bp.route("/nginx.conf", methods=["POST"])
@admin_required
def nginx_conf():
    if not Csrf().validate_on_submit():
        abort(400)
    from flask import current_app

    info = certs.current(service.tls_dir())
    if info is None:
        flash("Make or add a certificate first.", "warning")
        return redirect(url_for("https.index"))
    upstream = f"127.0.0.1:{request.form.get('port', type=int) or 5000}"
    if request.form.get("hsts") and info["self_signed"]:
        flash("HSTS with a self-signed certificate would lock browsers out of the site. Use a certificate from a certificate authority, or leave it off.", "danger")
        return _page(), 400
    try:
        text = nginx.render(names=request.form.get("names") or info["names"],
                            cert=request.form.get("cert_path") or str(service.tls_dir() / certs.CERT_FILE),
                            key=request.form.get("key_path") or str(service.tls_dir() / certs.KEY_FILE),
                            upstream=upstream, max_upload_mb=max(1, int(current_app.config["MAX_CONTENT_LENGTH"]) // (1024 * 1024)),
                            redirect_http=bool(request.form.get("redirect")), hsts=bool(request.form.get("hsts")),
                            standalone=request.form.get("kind") == "standalone")
    except certs.CertError as e:
        flash(str(e), "danger")
        return _page(), 400
    resp = Response(text, mimetype="text/plain; charset=utf-8")
    resp.headers["Content-Disposition"] = 'attachment; filename="nginx.conf"'
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/certificate.crt")
@admin_required
def certificate():
    """The public certificate (never the key), to install on a PC or phone that should trust a self-signed one."""
    try:
        data = (service.tls_dir() / certs.CERT_FILE).read_bytes()
    except OSError:
        abort(404)
    resp = Response(data, mimetype="application/x-x509-ca-cert")
    resp.headers["Content-Disposition"] = 'attachment; filename="aisignx-server.crt"'
    return resp
