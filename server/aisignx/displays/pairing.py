"""Pairing a screen: the device shows a short code, an editor types it in the admin,
and the device's next poll claims a long-lived token.

The code is only a pointer that signed-in editors type. Claiming needs the 256-bit
pairing secret, which only the device knows. Nothing here commits; callers do.
"""
import hashlib
import secrets
from datetime import timedelta

from flask import current_app
from sqlalchemy import delete, func, update

from ..extensions import db
from ..models import Display, PairingCode
from ..player.auth import hash_token, new_token
from ..timeutil import as_utc, utcnow

# 31 symbols without 0/O/1/I/L, so 31^6 ~ 8.9e8 codes.
ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
CODE_LENGTH = 6
_CODE_TRIES = 5

PENDING = "pending"
EXPIRED = "expired"
PAIRED = "paired"


class TooManyPending(Exception):
    """No room for another pending pairing code right now."""


def display_code(code: str) -> str:
    """"K7PQ4X" -> "K7P-Q4X" for showing on screen."""
    half = len(code) // 2
    return f"{code[:half]}-{code[half:]}"


def normalize_code(value: str | None) -> str | None:
    """Strip spaces and dashes, upper-case. None unless it is a well-formed code."""
    if not value:
        return None
    code = "".join(value.split()).replace("-", "").upper()
    if len(code) != CODE_LENGTH or any(ch not in ALPHABET for ch in code):
        return None
    return code


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))


def _pending_count(*criteria) -> int:
    query = db.session.query(func.count(PairingCode.id)).filter(
        PairingCode.display_id.is_(None), *criteria)
    return query.scalar() or 0


def start(ip: str | None, client_id: str | None, device_info: str | None):
    """Create a pending code for a device. Returns (row, raw_secret).

    Raises TooManyPending when the global cap is reached or no free code was found.
    """
    cfg = current_app.config
    now = utcnow()
    # Expired rows (approved-but-unclaimed ones too) are purged here; no sweeper needed.
    db.session.execute(delete(PairingCode).where(PairingCode.expires_at < now))

    # A device asking again (page reload, expired code) replaces its own pending code.
    if client_id:
        db.session.execute(delete(PairingCode).where(PairingCode.client_id == client_id,
                                                     PairingCode.display_id.is_(None)))
    # Caps refuse rather than evict: evicting another screen's code would make screens
    # behind one IP (NAT, an unconfigured proxy) knock each other out forever.
    if ip and _pending_count(PairingCode.client_ip == ip) >= cfg["PAIRING_MAX_PENDING_PER_IP"]:
        raise TooManyPending()
    if _pending_count() >= cfg["PAIRING_MAX_PENDING"]:
        raise TooManyPending()

    for _ in range(_CODE_TRIES):
        code = _new_code()
        if db.session.query(PairingCode.id).filter_by(code=code).first() is None:
            break
    else:
        raise TooManyPending()

    secret = secrets.token_urlsafe(32)
    row = PairingCode(
        code=code, secret_hash=hash_secret(secret),
        client_ip=(ip or "")[:45] or None, client_id=client_id, device_info=device_info,
        created_at=now, expires_at=now + timedelta(seconds=cfg["PAIRING_CODE_TTL_SECONDS"]),
    )
    db.session.add(row)
    db.session.flush()  # the unique index on code is the final guard
    return row, secret


def approve(code: str | None, name: str | None, playlist_id: int | None,
            replace_display: Display | None, user=None) -> Display | None:
    """Attach a pending code to a new or existing display. None if the code is not usable.

    When replace_display is given its old credential is cleared; the caller must
    commit and then call broker.close(display.id). On None the caller rolls back.
    """
    normalized = normalize_code(code)
    if normalized is None:
        return None
    now = utcnow()
    row = PairingCode.query.filter(PairingCode.code == normalized,
                                   PairingCode.display_id.is_(None)).first()
    if row is None or as_utc(row.expires_at) <= now:
        return None

    if replace_display is not None:
        display = replace_display
        display.token_hash = None
        display.paired_at = None
        # An earlier approval for this display that was never claimed must not win later.
        db.session.execute(delete(PairingCode).where(PairingCode.display_id == display.id)
                           .execution_options(synchronize_session=False))
    else:
        display = Display(name=(name or "").strip()[:100] or "Display", playlist_id=playlist_id)
        db.session.add(display)
    db.session.flush()

    claim_until = now + timedelta(seconds=current_app.config["PAIRING_CLAIM_SECONDS"])
    res = db.session.execute(
        update(PairingCode)
        .where(PairingCode.id == row.id, PairingCode.display_id.is_(None))
        .values(display_id=display.id, expires_at=claim_until)
        .execution_options(synchronize_session=False)
    )
    if res.rowcount != 1:
        return None
    display.device_info = row.device_info
    return display


def claim(secret: str):
    """Device poll. Returns (PENDING, expires_in), (EXPIRED, None) or (PAIRED, (display, raw))."""
    row = PairingCode.query.filter_by(secret_hash=hash_secret(secret)).first()
    if row is None:
        return EXPIRED, None
    now = utcnow()
    expires_at = as_utc(row.expires_at)
    if expires_at <= now:
        db.session.delete(row)
        return EXPIRED, None
    if row.display_id is None:
        return PENDING, max(0, int((expires_at - now).total_seconds()))

    display_id = row.display_id
    res = db.session.execute(delete(PairingCode).where(PairingCode.id == row.id)
                             .execution_options(synchronize_session=False))
    if res.rowcount != 1:  # another poll claimed it first
        return EXPIRED, None
    db.session.expunge(row)
    display = db.session.get(Display, display_id)
    if display is None:
        return EXPIRED, None
    raw = new_token()
    display.token_hash = hash_token(raw)
    display.paired_at = now
    return PAIRED, (display, raw)
