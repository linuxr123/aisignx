"""The signing key of an Android APK (the SHA-256 of its first signer's certificate), read from the APK Signing Block (schemes v2 and v3).

Android installs an update only when the new APK is signed with the same key as the installed app. An app reports its own key digest; the
server compares it with the installer it is about to push and refuses a pair that Android would refuse anyway (docs/android-kiosk.md,
"Signing keys"). Reads only the end of the file; returns None for anything it does not understand (then nothing is blocked).
"""
from __future__ import annotations

import hashlib
import os
import struct

_MAGIC = b"APK Sig Block 42"
_IDS = (0x7109871A, 0xF05368C0, 0x1B93AD61)         # signing schemes v2, v3, v3.1
_EOCD = b"PK\x05\x06"
_MAX_BLOCK = 4 * 1024 * 1024


def _u32(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _first_cert(value: bytes) -> bytes | None:
    """value = the signer sequence of a v2/v3 block -> DER of the first certificate of the first signer."""
    try:
        seq_len = _u32(value, 0)
        signer_len = _u32(value, 4)
        signer = value[8:8 + signer_len]
        signed_len = _u32(signer, 0)
        signed = signer[4:4 + signed_len]
        digests_len = _u32(signed, 0)
        o = 4 + digests_len
        certs_len = _u32(signed, o)
        o += 4
        cert_len = _u32(signed, o)
        cert = signed[o + 4:o + 4 + cert_len]
        return cert if seq_len and cert and cert_len <= certs_len else None
    except (struct.error, IndexError):
        return None


def signer_sha256(path) -> str | None:
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - 70000))
            tail = f.read()
            i = tail.rfind(_EOCD)
            if i < 0 or i + 22 > len(tail):
                return None
            cd_offset = _u32(tail, i + 16)
            if cd_offset < 32 or cd_offset > size:
                return None
            f.seek(cd_offset - 24)
            footer = f.read(24)
            if footer[8:] != _MAGIC:
                return None
            block_size = struct.unpack_from("<Q", footer, 0)[0]
            if not 24 <= block_size <= _MAX_BLOCK or block_size + 8 > cd_offset:
                return None
            f.seek(cd_offset - block_size - 8)
            block = f.read(block_size + 8)
    except OSError:
        return None
    pairs = block[8:-24]
    found = {}
    o = 0
    try:
        while o + 12 <= len(pairs):
            length = struct.unpack_from("<Q", pairs, o)[0]
            ident = _u32(pairs, o + 8)
            if length < 4 or o + 8 + length > len(pairs):
                return None
            found[ident] = pairs[o + 12:o + 8 + length]
            o += 8 + length
    except struct.error:
        return None
    for ident in _IDS:
        if ident in found:
            cert = _first_cert(found[ident])
            if cert:
                return hashlib.sha256(cert).hexdigest()
    return None
