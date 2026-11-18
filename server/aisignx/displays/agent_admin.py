"""Admin actions for the privileged Windows agent (docs/windows-agent-service.md): enrollment code, removal,
and the automatic sign-in form. Admin role only: this is permission to run code as SYSTEM on that PC."""
import secrets

from flask import abort, flash, make_response, render_template, request
from flask_login import current_user
from ..audit import service as audit
from ..auth import ratelimit
from ..auth.decorators import admin_required
from ..extensions import db
from ..models.device_command import ACTION_SET_AUTOLOGIN
from . import agent, bp, ecies, enroll, signing
from .agent_admin_forms import AutologinForm
from .routes import _back, _get_display_or_404, _publish, _require_action_form

MAX_PASSWORD = 127
_LOOPBACK = ("127.0.0.1", "::1")


def _private_enough() -> bool:
    """The Windows password crosses the network once, in this request: https (or this very machine) only."""
    if request.is_secure:
        return True
    forwarded = any(request.headers.get(h) for h in ("X-Forwarded-For", "Forwarded", "X-Forwarded-Proto", "X-Real-IP"))
    return request.remote_addr in _LOOPBACK and not forwarded   # a same-host proxy does not make http private


@bp.route("/<int:display_id>/agent-enroll/code", methods=["POST"])
@admin_required
def agent_enroll_code(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    if not display.is_paired:
        abort(400)
    code = enroll.new_code(display)
    audit.record("display.agent_enroll_code", display)
    db.session.commit()
    resp = make_response(render_template("displays/agent_enroll.html", display=display, code=code,
                                         server=request.host_url.rstrip("/"), fingerprint=signing.fingerprint(),
                                         minutes=int(enroll.CODE_TTL.total_seconds() // 60)))
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/<int:display_id>/agent-enroll/remove", methods=["POST"])
@admin_required
def agent_remove(display_id: int):
    _require_action_form()
    display = _get_display_or_404(display_id)
    enroll.remove(display)
    audit.record("display.agent_removed", display, detail={"by": current_user.username})
    db.session.commit()
    flash("Privileged agent removed from this display. Uninstall it on the PC too (Install-Agent.ps1 -Uninstall).",
          "success")
    return _back(display)


@bp.route("/<int:display_id>/autologin", methods=["POST"])
@admin_required
def agent_autologin(display_id: int):
    """Set or undo automatic Windows sign-in. The password is encrypted for this PC's agent and only the
    ciphertext is stored; the password itself never reaches the database, the audit log or a flash message."""
    form = AutologinForm()
    if not form.validate_on_submit():
        abort(400)
    display = _get_display_or_404(display_id)
    undo = request.form.get("do") == "undo"
    caps = agent.capabilities(display)
    diag = enroll.diag_of(display)
    kiosk = str(diag.get("kiosk_user") or "")

    def refuse(message):
        flash(message, "warning")
        return _back(display)

    if not enroll.is_enrolled(display):
        return refuse("Enroll the privileged agent first.")
    if not undo:
        if not _private_enough():
            return refuse("Open this page over https to send a Windows password (it is sent once, to this server).")
        password = form.password.data or ""
        ukey = ratelimit.user_key(current_user.username)
        if ratelimit.login_blocked(request.remote_addr, ukey):
            return refuse("Too many wrong passwords. Wait a few minutes.")
        if not form.confirm.data or not current_user.check_password(form.confirm.data):
            ratelimit.login_failed(request.remote_addr, ukey)
            audit.record("auth.login_failed", detail={"username": ukey[:64], "via": "autologin confirm"})
            db.session.commit()
            return refuse("Your AiSignX password is not right.")
        ratelimit.login_succeeded(ukey)
        if len(password.encode("utf-8")) > ecies.MAX_PLAINTEXT:
            return refuse("That password is too long once encoded (256 bytes at most).")
        if not 1 <= len(password) <= MAX_PASSWORD or "\x00" in password:
            return refuse("The Windows password must be 1 to 127 characters.")
        if not kiosk or (form.user.data or "").strip() != kiosk:
            return refuse("The account must be the one this agent was installed for.")
        pub = ecies.decode_public(display.agent_pubkey or "")
        if pub is None:
            return refuse("The agent has no usable key. Enroll it again.")
        request_id = secrets.token_hex(8)
        try:
            env = ecies.encrypt(pub, password.encode("utf-8"), ecies.aad(display.id, request_id, kiosk, "."))
        except ValueError:
            return refuse("Could not encrypt for this agent. Enroll it again.")
        password = None
        params = {"enabled": True, "user": kiosk, "domain": ".", "key_id": ecies.key_id(pub), "enc": env}
    else:
        request_id, params = None, {"enabled": False}
    cmd, reason = agent.create(display, ACTION_SET_AUTOLOGIN, current_user, params=params, request_id=request_id)
    if cmd is None:
        return refuse(reason)
    # Never params: the envelope is not an audit detail.
    audit.record("display.agent_command", display,
                 detail={"command": ACTION_SET_AUTOLOGIN, "request_id": cmd.request_id,
                         "enabled": not undo, "user": kiosk or None})
    db.session.commit()
    _publish(display, {"action": "commands"}, force=True)
    flash("Automatic sign-in removal sent to the agent." if undo else
          "Automatic sign-in sent to the agent (encrypted). It applies at the next poll, within a minute.", "success")
    return _back(display)
