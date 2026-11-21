"""The approval flow that connects a freshly installed agent to its display (see models/agent_request.py).

Nothing here commits; callers do. The agent token is made at approval, stored hashed on the display like with the
old code flow, and only ever leaves the server encrypted to the agent's own key.
"""
from __future__ import annotations

import base64
import hashlib
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import update

from ..extensions import db
from ..models import AgentRequest, Display
from ..timeutil import as_utc, utcnow
from . import ecies, enroll

REQUEST_TTL = timedelta(hours=24)       # how long an installed agent waits for its approval
KEEP_APPROVED = timedelta(minutes=15)   # the encrypted token waits this long for the agent to collect it
MAX_PENDING = 50
_ID = re.compile(r"^[A-Za-z0-9_-]{22,64}$")


def _h(kind: str, value: str) -> str:
    return hashlib.sha256(f"aisignx-agent-{kind}|{value}".encode()).hexdigest()


def valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID.match(value))


def purge(now: datetime | None = None) -> None:
    now = now or utcnow()
    AgentRequest.query.filter(AgentRequest.expires_at <= now).delete(synchronize_session=False)


def create(claim_id, poll_secret, public_b64, host: str, version: str, now: datetime | None = None):
    """-> (request, "") or (None, reason). Asking again with the same ids is harmless."""
    now = now or utcnow()
    if not (valid_id(claim_id) and valid_id(poll_secret) and claim_id != poll_secret):
        return None, "bad id"
    if ecies.decode_public(public_b64) is None:
        return None, "bad public key"
    purge(now)
    existing = AgentRequest.query.filter_by(claim_hash=_h("claim", claim_id)).first()
    if existing is not None:
        return (existing, "") if existing.poll_hash == _h("poll", poll_secret) else (None, "id in use")
    if AgentRequest.query.filter_by(state="pending").count() >= MAX_PENDING:
        return None, "too many pending requests"
    req = AgentRequest(claim_hash=_h("claim", claim_id), poll_hash=_h("poll", poll_secret), public_key=public_b64,
                       host=(host or "")[:64], version=(version or "")[:20], created_at=now, expires_at=now + REQUEST_TTL)
    db.session.add(req)
    return req, ""


def claim(display: Display, claim_id, now: datetime | None = None):
    """The player of `display` says: the agent with this claim id is on my PC. -> (request, "") or (None, reason)."""
    now = now or utcnow()
    if not valid_id(claim_id):
        return None, "bad id"
    req = AgentRequest.query.filter_by(claim_hash=_h("claim", claim_id)).first()
    if req is None or as_utc(req.expires_at) <= now:
        return None, "unknown request"
    if req.display_id not in (None, display.id):
        return None, "already claimed"
    req.display_id = display.id
    if req.state == "pending" and display.agent_auto_approve and display.is_paired:
        approve(req, now)
        db.session.execute(update(Display).where(Display.id == display.id).values(agent_auto_approve=False)
                           .execution_options(synchronize_session=False))
    return req, ""


def pending_for(display: Display, now: datetime | None = None) -> list[AgentRequest]:
    now = now or utcnow()
    return (AgentRequest.query.filter(AgentRequest.display_id == display.id, AgentRequest.state == "pending",
                                      AgentRequest.expires_at > now).order_by(AgentRequest.created_at).all())


def approve(req: AgentRequest, now: datetime | None = None) -> None:
    """Makes the agent token for req's display and leaves it, encrypted, for the agent to collect."""
    now = now or utcnow()
    display = req.display
    token = enroll.TOKEN_PREFIX + secrets.token_urlsafe(32)
    pub = ecies.decode_public(req.public_key)
    db.session.execute(update(Display).where(Display.id == display.id).values(
        agent_enroll_hash=None, agent_enroll_expires=None, agent_enroll_fails=0, agent_token_hash=enroll.hash_token(token),
        agent_pubkey=base64.b64encode(pub).decode("ascii"), agent_diag=None, agent_seen_at=now, agent_enrolled_at=now)
        .execution_options(synchronize_session=False))
    enroll.cancel_open_tier_b(display.id)
    req.ciphertext = ecies.encrypt(pub, token.encode("ascii"), aad(req))
    req.state = "approved"
    req.expires_at = now + KEEP_APPROVED
    # Any other waiting request of this display is stale now (a new agent replaced the old one).
    AgentRequest.query.filter(AgentRequest.display_id == display.id, AgentRequest.id != req.id,
                              AgentRequest.state == "pending").delete(synchronize_session=False)


def reject(req: AgentRequest) -> None:
    req.state = "rejected"
    req.ciphertext = None


def aad(req: AgentRequest) -> bytes:
    return b"aisignx-agent-approval|" + req.claim_hash.encode("ascii")


def poll(poll_secret, now: datetime | None = None):
    """-> (state, request or None): "unknown", "pending", "approved" or "rejected"."""
    now = now or utcnow()
    if not valid_id(poll_secret):
        return "unknown", None
    req = AgentRequest.query.filter_by(poll_hash=_h("poll", poll_secret)).first()
    if req is None or as_utc(req.expires_at) <= now:
        return "unknown", None
    return req.state, req
