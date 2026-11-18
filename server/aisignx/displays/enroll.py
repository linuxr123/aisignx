"""Enrolling the privileged Windows agent (docs/windows-agent-service.md).

An admin asks for a one-time code on the display page. The elevated install script on the PC sends it to
POST /api/player/v1/agent/enroll together with the agent's public key and gets back an agent token, which is
a different credential from the display token. Only sha256 of the code and of the token is stored.

All writes here are Core UPDATEs: an ORM write to a Display would broadcast a manifest hint to the screen.
Nothing here commits; callers do.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from datetime import datetime, timedelta

from sqlalchemy import update

from ..extensions import db
from ..models import DeviceCommand, Display
from ..models.device_command import OPEN_STATES, SIGNED_ACTIONS, STATE_CANCELLED
from ..timeutil import as_utc, utcnow
from . import ecies

TOKEN_PREFIX = "agt_"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"   # 32 symbols = 5 bits each
CODE_LENGTH = 16                                      # 80 bits
CODE_TTL = timedelta(minutes=15)
MAX_FAILS = 5                                         # kept for old rows; a wrong code strikes nobody (the 80-bit code is the defence, the per-address limit stops floods)
STALE_AFTER = timedelta(minutes=10)                   # the agent polls every 20 s
_TOKEN = re.compile(r"^agt_[A-Za-z0-9_-]{43}$")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_token(token: str) -> str:
    return _sha(token)


def normalize_code(text) -> str | None:
    """The 16 symbols of a code typed with or without dashes and spaces, or None."""
    if not isinstance(text, str) or len(text) > 40:
        return None
    code = re.sub(r"[\s-]", "", text).upper()
    return code if len(code) == CODE_LENGTH and all(c in CODE_ALPHABET for c in code) else None


def _hash_code(code: str) -> str:
    return _sha("aisignx-agent-enroll|" + code)


def format_code(code: str) -> str:
    return "-".join(code[i:i + 4] for i in range(0, CODE_LENGTH, 4))


def new_code(display: Display, now: datetime | None = None) -> str:
    """A fresh one-time code for `display` (it replaces an earlier unused one). Returned once, stored hashed."""
    now = now or utcnow()
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    db.session.execute(update(Display).where(Display.id == display.id).values(
        agent_enroll_hash=_hash_code(code), agent_enroll_expires=now + CODE_TTL, agent_enroll_fails=0)
        .execution_options(synchronize_session=False))
    return format_code(code)


def enroll(code_text, public_b64, now: datetime | None = None) -> tuple[Display | None, str | None, str]:
    """(display, agent_token, "") or (None, None, reason). The code is burned atomically, so it works once."""
    now = now or utcnow()
    code = normalize_code(code_text)
    pub = ecies.decode_public(public_b64) if isinstance(public_b64, str) else None
    if pub is None:
        return None, None, "bad public key"
    if code is None:
        return None, None, "invalid code"
    digest = _hash_code(code)
    display = Display.query.filter_by(agent_enroll_hash=digest).first()
    if (display is None or not display.is_paired or display.agent_enroll_fails >= MAX_FAILS
            or display.agent_enroll_expires is None or as_utc(display.agent_enroll_expires) <= now):
        return None, None, "invalid code"
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    burned = db.session.execute(update(Display).where(
        Display.id == display.id, Display.agent_enroll_hash == digest, Display.agent_enroll_expires > now,
        Display.agent_enroll_fails < MAX_FAILS).values(
        agent_enroll_hash=None, agent_enroll_expires=None, agent_enroll_fails=0, agent_token_hash=_sha(token),
        agent_pubkey=base64.b64encode(pub).decode("ascii"), agent_diag=None, agent_seen_at=now, agent_enrolled_at=now)
        .execution_options(synchronize_session=False))
    if burned.rowcount != 1:       # somebody else used it a moment ago
        return None, None, "invalid code"
    cancel_open_tier_b(display.id)  # nothing queued for the old way of doing things survives a re-enroll
    return display, token, ""


def agent_for_token(token: str | None) -> Display | None:
    if not isinstance(token, str) or not _TOKEN.match(token):
        return None
    display = Display.query.filter_by(agent_token_hash=hash_token(token)).first()
    return display if display is not None and display.is_paired else None


def cancel_open_tier_b(display_id: int) -> None:
    """Open signed commands are cancelled, and any ciphertext in them removed."""
    db.session.execute(update(DeviceCommand).where(
        DeviceCommand.display_id == display_id, DeviceCommand.action.in_(SIGNED_ACTIONS),
        DeviceCommand.state.in_(OPEN_STATES)).values(state=STATE_CANCELLED, params=None, result="Agent changed.")
        .execution_options(synchronize_session=False))


def remove(display: Display) -> None:
    db.session.execute(update(Display).where(Display.id == display.id).values(
        agent_token_hash=None, agent_enroll_hash=None, agent_enroll_expires=None, agent_enroll_fails=0,
        agent_pubkey=None, agent_diag=None, agent_seen_at=None, agent_enrolled_at=None)
        .execution_options(synchronize_session=False))
    cancel_open_tier_b(display.id)


def touch(display: Display, now: datetime | None = None) -> None:
    db.session.execute(update(Display).where(Display.id == display.id).values(agent_seen_at=now or utcnow())
                       .execution_options(synchronize_session=False))


def store_diag(display: Display, text: str | None, now: datetime | None = None) -> None:
    values = {"agent_seen_at": now or utcnow()}
    if text is not None:
        values["agent_diag"] = text
    db.session.execute(update(Display).where(Display.id == display.id).values(**values)
                       .execution_options(synchronize_session=False))


def is_enrolled(display: Display) -> bool:
    return bool(display.agent_token_hash)


def diag_of(display: Display) -> dict:
    try:
        data = json.loads(display.agent_diag or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def summary(display: Display, now: datetime | None = None) -> dict:
    """What the display page shows about the privileged agent."""
    now = now or utcnow()
    enrolled = is_enrolled(display)
    seen = as_utc(display.agent_seen_at) if display.agent_seen_at else None
    code_live = bool(display.agent_enroll_hash and display.agent_enroll_expires
                     and as_utc(display.agent_enroll_expires) > now and display.agent_enroll_fails < MAX_FAILS)
    return {"enrolled": enrolled, "seen_at": seen, "code_live": code_live, "diag": diag_of(display) if enrolled else {},
            "stale": enrolled and (seen is None or now - seen > STALE_AFTER),
            "seen_min": int((now - seen).total_seconds() // 60) if seen else None}
