"""
Plugin signing & registry verification - Phase 4.

Goal
----
Detect plugin tampering and refuse to load plugins whose code has changed
since they were last signed. Signing is per-installation (HMAC-based, not
PKI) so it is suitable for single-organization deployments where one
operator is trusted to mint signatures. For multi-org distribution the
shared-secret can be exported and added to a peer server's trust list.

Why HMAC, not Ed25519
---------------------
The base install ships only the dependencies in `requirements.txt`. Adding
`cryptography` (large native dep, slow on Windows pip installs) just for
optional plugin signing is too expensive. HMAC-SHA256 over a per-server
shared secret gives us tamper detection and simple admin tooling with
nothing but the stdlib. Upgrading to Ed25519 later is a one-file change.

Algorithm
---------
The "manifest hash" of a plugin is a deterministic digest of:

    1. The plugin folder name
    2. SHA-256 of each file under the plugin folder, sorted by path,
       except `plugin.json` itself and any file beginning with `.`
    3. A canonical JSON serialization of plugin.json with the
       `signature` and `signed_files_hash` fields removed.

The signature is `hex(HMAC-SHA256(secret_bytes, manifest_hash))`.
A plugin is "trusted" if at least one secret in the trust list verifies
its `signature` AND the plugin's reported `signed_files_hash` matches
the recomputed manifest hash.

Settings
--------
    plugin.signing.secret          - hex string, auto-generated on first boot
    plugin.signing.trust_list      - JSON list of accepted hex secrets
                                     (the local secret is always trusted)
    plugin.signing.require_signed  - bool; when true, unsigned plugins are
                                     refused. Default false (back-compat).

Status field on each plugin meta
--------------------------------
    signature_status:  'valid' | 'invalid' | 'unsigned' | 'missing_secret'
    signature_detail:  human-readable diagnostic
"""
import hashlib
import hmac
import json
import secrets
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

from logging_config import logger
import settings as settings_module


# Setting keys
_KEY_SECRET     = 'plugin.signing.secret'
_KEY_TRUST_LIST = 'plugin.signing.trust_list'
_KEY_REQUIRE    = 'plugin.signing.require_signed'

# Files inside the plugin folder that are not part of the signed manifest.
_HASH_EXCLUDE_BASENAMES = {'plugin.json'}


# ---------------------------------------------------------------------------
# Secret + trust list bootstrapping
# ---------------------------------------------------------------------------
def get_local_secret() -> str:
    """Return the local plugin-signing secret as a hex string. Generated on
    first call (or when blank). Never raises."""
    val = settings_module.effective_value(_KEY_SECRET) or ''
    if val:
        return val
    val = secrets.token_hex(32)
    try:
        settings_module.set(_KEY_SECRET, val, _allow_unknown=True,
                            value_type='string', is_sensitive=True)
        logger.info('plugin-signing: generated local secret')
    except Exception:
        logger.exception('plugin-signing: failed to persist generated secret')
    return val


def trusted_secrets() -> List[str]:
    """All hex secrets accepted for verification. The local secret is
    always included even if not explicitly listed."""
    raw = settings_module.effective_value(_KEY_TRUST_LIST)
    out = []
    if isinstance(raw, list):
        for s in raw:
            if isinstance(s, str) and s.strip():
                out.append(s.strip().lower())
    local = (get_local_secret() or '').lower()
    if local and local not in out:
        out.append(local)
    return out


def require_signed() -> bool:
    return bool(settings_module.effective_value(_KEY_REQUIRE))


# ---------------------------------------------------------------------------
# Manifest hashing
# ---------------------------------------------------------------------------
def _iter_signable_files(plugin_dir: Path):
    """Yield (relative_path_str, absolute_path) for every file under
    plugin_dir that contributes to the signed hash."""
    for p in sorted(plugin_dir.rglob('*')):
        if not p.is_file():
            continue
        # Skip plugin.json itself; we hash a sanitized copy of its JSON.
        if p.name in _HASH_EXCLUDE_BASENAMES and p.parent == plugin_dir:
            continue
        # Skip dotfiles / hidden directories.
        rel = p.relative_to(plugin_dir)
        parts = rel.parts
        if any(part.startswith('.') for part in parts):
            continue
        yield (str(rel).replace('\\', '/'), p)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _manifest_canonical(meta_raw: Dict[str, Any]) -> str:
    """Return a canonical JSON string of plugin.json minus the signature
    fields, used as part of the hashed input."""
    clone = dict(meta_raw)
    clone.pop('signature', None)
    clone.pop('signed_files_hash', None)
    return json.dumps(clone, sort_keys=True, separators=(',', ':'))


def compute_manifest_hash(plugin_dir: Path,
                          meta_raw: Dict[str, Any]) -> str:
    """Deterministic hash of folder name + every file's content + cleaned
    plugin.json. Returns a hex string."""
    h = hashlib.sha256()
    h.update(plugin_dir.name.encode('utf-8'))
    h.update(b'\x00')
    for rel, abs_p in _iter_signable_files(plugin_dir):
        h.update(rel.encode('utf-8'))
        h.update(b':')
        h.update(_sha256_file(abs_p).encode('ascii'))
        h.update(b'\n')
    h.update(b'\x00')
    h.update(_manifest_canonical(meta_raw).encode('utf-8'))
    return h.hexdigest()


def sign_hash(manifest_hash: str, secret_hex: Optional[str] = None) -> str:
    """HMAC-SHA256 of manifest_hash, hex-encoded."""
    sec = (secret_hex or get_local_secret() or '').strip()
    try:
        key = bytes.fromhex(sec)
    except ValueError:
        raise ValueError('plugin signing secret is not valid hex')
    return hmac.new(key, manifest_hash.encode('ascii'),
                    hashlib.sha256).hexdigest()


# ---------------------------------------------------------------------------
# Verification (called by plugin_system._load_registry)
# ---------------------------------------------------------------------------
def verify_plugin(plugin_dir: Path, meta_raw: Dict[str, Any]
                   ) -> Tuple[str, str]:
    """Return (status, detail) for one plugin folder.
        status in {'valid', 'invalid', 'unsigned', 'missing_secret'}

    The hash is computed against the *on-disk* plugin.json (re-read here)
    so that registry-time augmentation of `meta_raw` (default fields,
    normalized permissions, etc.) does not perturb the canonical form.
    """
    sig = (meta_raw.get('signature') or '').strip().lower()
    declared_hash = (meta_raw.get('signed_files_hash') or '').strip().lower()
    if not sig and not declared_hash:
        return 'unsigned', 'no signature in plugin.json'
    if not sig or not declared_hash:
        return 'invalid', 'partial signature: both fields required'

    try:
        on_disk = json.loads((plugin_dir / 'plugin.json')
                              .read_text(encoding='utf-8-sig'))
        actual_hash = compute_manifest_hash(plugin_dir, on_disk)
    except Exception as e:
        return 'invalid', f'hash failed: {e}'

    if not hmac.compare_digest(actual_hash, declared_hash):
        return 'invalid', 'files changed since signing'

    secrets_list = trusted_secrets()
    if not secrets_list:
        return 'missing_secret', 'no trusted secrets configured'
    for sec in secrets_list:
        try:
            expected = sign_hash(actual_hash, sec)
        except ValueError:
            continue
        if hmac.compare_digest(expected, sig):
            return 'valid', 'signature verified'
    return 'invalid', 'no trusted secret produced this signature'


# ---------------------------------------------------------------------------
# Admin tooling
# ---------------------------------------------------------------------------
def sign_plugin_in_place(plugin_dir: Path) -> Dict[str, Any]:
    """Compute the manifest hash, sign with the local secret, and write
    `signature` + `signed_files_hash` into plugin.json. Returns a summary
    dict. Raises on I/O error."""
    meta_path = plugin_dir / 'plugin.json'
    raw_text = meta_path.read_text(encoding='utf-8-sig')
    meta_raw = json.loads(raw_text)
    # Strip any prior signature so we hash only the source-of-truth fields.
    meta_raw.pop('signature', None)
    meta_raw.pop('signed_files_hash', None)
    h = compute_manifest_hash(plugin_dir, meta_raw)
    sig = sign_hash(h)
    meta_raw['signed_files_hash'] = h
    meta_raw['signature'] = sig
    meta_path.write_text(
        json.dumps(meta_raw, indent=2, ensure_ascii=False),
        encoding='utf-8',
    )
    return {
        'plugin':            plugin_dir.name,
        'signed_files_hash': h,
        'signature':         sig,
    }
