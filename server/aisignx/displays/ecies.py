"""ECIES-style envelope for the auto-login password (docs/signed-commands.md, "Auto-login envelope").

Only the privileged Windows agent holds the matching private key, so the server stores nothing but ciphertext.

    eph      = fresh X25519 key pair
    shared   = X25519(eph_private, agent_public)
    key      = HKDF-SHA256(shared, salt = eph_public || agent_public, info = "aisignx-autologin-v1", 32 bytes)
    envelope = base64( eph_public(32) || nonce(12) || AES-256-GCM(key, nonce, plaintext, aad) )   # ciphertext||tag

The associated data binds the envelope to one display, one request, one account:
    aad = "v1|<display_id>|<request_id>|<user>|<domain>"   (UTF-8)

The Node twin is clients/electron-client/src/agent-svc/ecies.js; both are tested against the same vectors.
"""
import base64
import hashlib
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

INFO = b"aisignx-autologin-v1"
MAX_PLAINTEXT = 256
_RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def aad(display_id: int, request_id: str, user: str, domain: str) -> bytes:
    return f"v1|{int(display_id)}|{request_id}|{user}|{domain}".encode("utf-8")


def key_id(public_raw: bytes) -> str:
    """16 hex characters: how the agent says which of its keys an envelope is for."""
    return hashlib.sha256(public_raw).hexdigest()[:16]


def decode_public(public_b64: str) -> bytes | None:
    try:
        raw = base64.b64decode(public_b64, validate=True)
    except (ValueError, TypeError):
        return None
    return raw if len(raw) == 32 else None


def _key(shared: bytes, eph_pub: bytes, agent_pub: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub + agent_pub, info=INFO).derive(shared)


def encrypt(agent_public: bytes, plaintext: bytes, associated: bytes, *, eph: X25519PrivateKey | None = None,
            nonce: bytes | None = None) -> str:
    """The envelope for `agent_public` (32 raw bytes). `eph` and `nonce` exist for test vectors only."""
    if len(agent_public) != 32 or not 0 < len(plaintext) <= MAX_PLAINTEXT:
        raise ValueError("bad input")
    eph = eph or X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(*_RAW)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(agent_public))  # ValueError on a low-order point
    nonce = nonce or os.urandom(12)
    sealed = AESGCM(_key(shared, eph_pub, agent_public)).encrypt(nonce, plaintext, associated)
    return base64.b64encode(eph_pub + nonce + sealed).decode("ascii")


def decrypt(private: X25519PrivateKey, envelope_b64: str, associated: bytes) -> bytes | None:
    """The plaintext, or None for anything wrong (the agent's rule: one answer for every failure)."""
    try:
        blob = base64.b64decode(envelope_b64, validate=True)
        if not 32 + 12 + 16 + 1 <= len(blob) <= 32 + 12 + 16 + MAX_PLAINTEXT:
            return None
        eph_pub, nonce, sealed = blob[:32], blob[32:44], blob[44:]
        agent_pub = private.public_key().public_bytes(*_RAW)
        shared = private.exchange(X25519PublicKey.from_public_bytes(eph_pub))
        return AESGCM(_key(shared, eph_pub, agent_pub)).decrypt(nonce, sealed, associated)
    except (ValueError, TypeError, InvalidTag):
        return None
