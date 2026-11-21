"""API of the privileged Windows agent: /api/player/v1/agent/* (docs/windows-agent-service.md).

A different credential (`agt_...`) from the display token, in a different namespace: a display token never works
here and an agent token never works on the display routes. The agent receives only the signed Tier B commands
and the installer of its own open update_app command.
"""
import threading
import time
from functools import wraps

from flask import g, jsonify, request, send_file

from ..audit import service as audit
from ..displays import agent, agent_request, ecies, enroll, signing
from ..extensions import db
from ..models import Display
from ..models.device_command import SIGNED_ACTIONS
from ..timeutil import as_utc, utcnow
from . import agent_diag, api_bp
from .auth import display_required
from .api import _bad, _body, _text, api_error

_MAX_TOKEN = 80
ENROLL_PER_MINUTE = 10
_hits: dict = {}
_lock = threading.Lock()


def reset_rate_limit() -> None:
    with _lock:
        _hits.clear()


def _rate_limited(ip: str) -> bool:
    now = time.monotonic()
    with _lock:
        recent = [t for t in _hits.get(ip, []) if now - t < 60]
        recent.append(now)
        _hits[ip] = recent
        if len(_hits) > 1000:  # keep the table small
            for key in [k for k, v in _hits.items() if not v or now - v[-1] >= 60]:
                _hits.pop(key, None)
        return len(recent) > ENROLL_PER_MINUTE


def _unauthorized():
    resp = jsonify(error="unauthorized", message="Unknown agent.")
    resp.status_code = 401
    resp.headers["Cache-Control"] = "no-store"
    return resp


def agent_required(view):
    """Only `Authorization: Bearer agt_...` (never a cookie, never a display token). Sets g.display."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        token = header[7:].strip() if header[:7].lower() == "bearer " else ""
        display = enroll.agent_for_token(token) if token and len(token) <= _MAX_TOKEN else None
        if display is None:
            return _unauthorized()
        g.display = display
        return view(*args, **kwargs)

    return wrapped


@api_bp.post("/agent/enroll")
def agent_enroll():
    if _rate_limited(request.remote_addr or "?"):
        return api_error(429, "too_many_pending", "Too many attempts. Wait a minute.", retry_after=60)
    if (request.content_length or 0) > 4096:
        return api_error(413, "too_large", "Request too large.")
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    prior = Display.query.filter_by(agent_enroll_hash=enroll._hash_code(enroll.normalize_code(body.get("code")) or "")).first()
    replaced = bool(prior is not None and prior.agent_token_hash)
    display, token, reason = enroll.enroll(body.get("code"), body.get("public_key"))
    db.session.commit()
    if display is None:
        return api_error(403 if reason == "invalid code" else 400, "enroll_refused", reason)
    audit.record_now("display.agent_enrolled", display,
                     detail={"fingerprint": ecies.key_id(ecies.decode_public(body.get("public_key"))),
                             "replaced": replaced, "host": _text(body.get("host"), 64), "version": _text(body.get("version"), 20)})
    return jsonify(agent_token=token, display_id=display.id, command_key=signing.public_key_b64(),
                   fingerprint=signing.fingerprint(), server_time_ms=int(utcnow().timestamp() * 1000))


@api_bp.post("/agent/request")
def agent_request_create():
    """A freshly installed agent asks to be enrolled (no code: an admin approves, see displays/agent_request.py)."""
    if _rate_limited(request.remote_addr or "?"):
        return api_error(429, "too_many_pending", "Too many attempts. Wait a minute.", retry_after=60)
    if (request.content_length or 0) > 4096:
        return api_error(413, "too_large", "Request too large.")
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    req, reason = agent_request.create(body.get("claim_id"), body.get("poll_secret"), body.get("public_key"),
                                       _text(body.get("host"), 64), _text(body.get("version"), 20))
    db.session.commit()
    if req is None:
        return api_error(429 if "many" in reason else 400, "request_refused", reason)
    return jsonify(ok=True, state=req.state)


@api_bp.post("/agent/claim")
@display_required
def agent_request_claim():
    """The player says which agent runs on its PC (the claim id the agent wrote where the player can read it)."""
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    req, reason = agent_request.claim(g.display, body.get("claim_id"))
    db.session.commit()
    if req is None:
        return api_error(404 if reason == "unknown request" else 409 if reason == "already claimed" else 400, "claim_refused", reason)
    if req.state == "approved":
        audit.record_now("display.agent_approved", g.display, detail={"host": req.host, "via": "pairing"})
    return jsonify(ok=True, state=req.state)


@api_bp.post("/agent/poll")
def agent_request_poll():
    """The agent asks whether it was approved. Approved: the token encrypted to its own key, and the command key."""
    if _rate_limited(request.remote_addr or "?"):
        return api_error(429, "too_many_pending", "Too many attempts. Wait a minute.", retry_after=60)
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    state, req = agent_request.poll(body.get("poll_secret"))
    if state == "unknown":
        return api_error(404, "unknown_request", "Unknown or expired request.")
    if state != "approved":
        return jsonify(state=state, claimed=req.display_id is not None)
    return jsonify(state="approved", ciphertext=req.ciphertext, display_id=req.display_id, command_key=signing.public_key_b64(),
                   fingerprint=signing.fingerprint(), server_time_ms=int(utcnow().timestamp() * 1000))


@api_bp.get("/agent/commands")
@agent_required
def agent_commands():
    cmds = agent.deliver(g.display, only=SIGNED_ACTIONS)
    now = utcnow()
    out = []
    for c in cmds:
        out.append({"id": c.request_id, "action": c.action, "params": {},
                    "expires_in_s": max(0, int((as_utc(c.expires_at) - now).total_seconds())),
                    "signed": signing.sign(c)})
        agent.scrub_delivered(c)   # after signing: the envelope is not kept
    enroll.touch(g.display, now)
    db.session.commit()
    return jsonify(commands=out, server_time_ms=int(now.timestamp() * 1000))


@api_bp.get("/agent/command-key")
@agent_required
def agent_command_key():
    return jsonify(public_key=signing.public_key_b64(), fingerprint=signing.fingerprint())


@api_bp.post("/agent/commands/<request_id>/ack")
@agent_required
def agent_ack(request_id: str):
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    cmd, error = agent.acknowledge(g.display, request_id, str(body.get("state") or ""),
                                   _text(body.get("result"), agent.MAX_RESULT), only=SIGNED_ACTIONS)
    db.session.commit()
    if cmd is None:
        status = 404 if error == "unknown command" else 409 if error in ("expired", "already acknowledged", "not open") else 400
        return api_error(status, "command_" + error.replace(" ", "_"), error)
    audit.record_now("display.agent_result", g.display, detail={"command": cmd.action, "state": cmd.state, "via": "agent"})
    return jsonify(ok=True, state=cmd.state)


@api_bp.get("/agent/commands/<request_id>/package")
@agent_required
def agent_package(request_id: str):
    row, found = agent.package_for(g.display, request_id)
    if row is None:
        status = 404 if found in ("unknown command", "the installer is gone") else 409
        return api_error(status, "package_" + found.replace(" ", "_"), found)
    audit.record_now("display.agent_result", g.display,
                     detail={"command": "update_app", "download": row.filename, "via": "agent"})
    resp = send_file(found, mimetype="application/octet-stream", as_attachment=True, download_name=row.filename,
                     max_age=0)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp


@api_bp.post("/agent/diagnostics")
@agent_required
def agent_diagnostics():
    body = _body()
    if body is None:
        return _bad("Expected a JSON object.")
    enroll.store_diag(g.display, agent_diag.clean(body.get("diag", body)))
    db.session.flush()
    db.session.refresh(g.display)
    if g.display.pending_run_as:   # a PC set up from the admin: its run mode goes out as soon as the agent can do it
        agent.apply_pending_run_mode(g.display)
    db.session.commit()
    return jsonify(ok=True, server_time_ms=int(utcnow().timestamp() * 1000))


@api_bp.get("/agent/status")
@agent_required
def agent_status():
    d = g.display
    seen = int((utcnow() - as_utc(d.last_seen_at)).total_seconds()) if d.last_seen_at else None
    return jsonify(player_version=d.player_version, player_last_seen_s=seen)


@api_bp.post("/agent/unenroll")
@agent_required
def agent_unenroll():
    display = g.display
    enroll.remove(display)
    db.session.commit()
    audit.record_now("display.agent_removed", display, detail={"by": "agent"})
    return jsonify(ok=True)
