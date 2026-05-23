"""
System settings - Phase 1, Task 5.

Versioned key/value store backed by the system_setting table. Two scopes:
    * global  : domain_id IS NULL    -- server-wide default
    * domain  : domain_id = X         -- per-tenant override

Precedence when reading a key with effective_value(key, domain_id):
    1. domain override   (system_setting where domain_id = X and key = K)
    2. global value      (system_setting where domain_id IS NULL and key = K)
    3. builtin default   (BUILTIN_DEFAULTS dict below)
    4. None

Auto-detected keys (from system_caps.all_auto_settings()) are stored as
global rows with is_auto=True. Admin overrides land in separate non-auto
rows; admin UI shows "auto: X | override: Y" so the original detected value
is always visible.

Deprecated keys are NEVER deleted. Add a comment in BUILTIN_DEFAULTS instead
so future code knows to ignore them. This keeps upgrades safe.
"""
import json

from sqlalchemy.exc import IntegrityError

from models import db, SystemSetting
from tenant_filter import bypass_tenant_filter


# -----------------------------------------------------------------------------
# Builtin defaults. Keys here are the ground truth -- if a setting isn't
# listed, it doesn't officially exist. Format:
#     'key': (default_value, value_type, is_sensitive, description)
# -----------------------------------------------------------------------------
BUILTIN_DEFAULTS = {
    # --- security ------------------------------------------------------------
    'security.allow_http_lan': (
        True, 'bool', False,
        'Allow the server to serve HTTP (no TLS). Default true for LAN '
        'installs; set false to require HTTPS.',
    ),
    'security.signing_key': (
        '', 'string', True,
        'HMAC secret for signed media URLs. Auto-generated on first boot.',
    ),
    'server.public_url': (
        '', 'string', False,
        'Public/LAN base URL clients should use to reach this AISignX server. '
        'Set this to your DNS name (preferred) or LAN IP. Used in downloaded '
        'client setup files so they do not contain localhost.',
    ),

    # --- timezone ------------------------------------------------------------
    'default_timezone': (
        'UTC', 'string', False,
        'Server-wide default IANA timezone. Used as fallback when a Schedule '
        'or Domain does not specify one.',
    ),

    # --- background jobs -----------------------------------------------------
    'job.max_concurrent_image_transcodes': (
        1, 'int', False, 'Max concurrent image transcode jobs.',
    ),
    'job.max_concurrent_video_jobs': (
        0, 'int', False, 'Max concurrent video jobs (Phase 2+, 0 = disabled).',
    ),

    # --- runtime tuning ------------------------------------------------------
    'sse.max_concurrent_connections': (
        200, 'int', False, 'Soft cap on concurrent SSE display connections.',
    ),
    'heartbeat.batch_seconds': (
        60, 'int', False, 'How often the server flushes batched heartbeats.',
    ),
    'upload.max_size_mb': (
        100, 'int', False, 'Maximum single-file upload size in MB.',
    ),
    'cache.in_memory_mb': (
        64, 'int', False, 'In-memory cache budget in MB.',
    ),

    # --- disk monitoring -----------------------------------------------------
    'disk.upload_root': (
        '', 'string', False,
        'Absolute or server-relative path where all tenant media is stored '
        '(d1/, d2/, …). Leave empty to use UPLOAD_FOLDER from config.py. '
        'Example: D:\\AISignX\\uploads or /mnt/signage/uploads. '
        'Use “Move existing files” when saving a new path to migrate d1/, d2/, … '
        'from the current location. Files already at the destination are skipped.',
    ),
    'disk.warn_pct': (
        80, 'int', False, 'Disk-usage warning threshold (percent).',
    ),
    'disk.block_uploads_pct': (
        95, 'int', False, 'Disk-usage threshold above which uploads are blocked.',
    ),

    # --- rate limiting -------------------------------------------------------
    'ratelimit.enabled': (
        True, 'bool', False,
        'Master switch for API/login rate limiting. Disable with extreme care.',
    ),
    'ratelimit.login_per_min': (
        5, 'int', False,
        'Max /login POST attempts per minute per client IP.',
    ),
    'ratelimit.register_per_min': (
        10, 'int', False,
        'Max /api/register POST attempts per minute per client IP.',
    ),
    'ratelimit.browser_register_per_min': (
        5, 'int', False,
        'Max browser self-registration attempts per minute per client IP.',
    ),
    'ratelimit.api_per_min': (
        600, 'int', False,
        'Max requests per minute per API token (or per IP for cookie callers).',
    ),

    # --- audit log retention ------------------------------------------------
    'audit.retention.enabled': (
        True, 'bool', False,
        'Master switch for periodic audit-log pruning. When false, no rows '
        'are ever deleted (table grows unbounded).',
    ),
    'audit.retention.default_days': (
        365, 'int', False,
        'Default age (in days) after which audit rows are deleted. Use 0 '
        'to keep forever by default (only the per-action overrides will prune).',
    ),
    'audit.retention.overrides': (
        {}, 'json', False,
        'Per-action retention overrides as a JSON object: '
        '{"action.key": days, "action.prefix.": days}. A trailing dot '
        'matches by prefix. Value 0 means keep forever for that action. '
        'Example: {"login.success": 30, "display.heartbeat": 7, "domain.": 1825}.',
    ),
    'audit.retention.purge_interval_hours': (
        24, 'int', False,
        'How often the audit retention sweep runs, in hours. Minimum 1.',
    ),
    'audit.retention.batch_size': (
        5000, 'int', False,
        'Max rows deleted per action in one sweep. Keeps each pass cheap; '
        'remaining rows roll over to the next sweep.',
    ),

    # --- backup automation ---------------------------------------------------
    'backup.location': (
        '', 'string', False,
        'Filesystem path where backup .zip archives are written. Leave '
        'empty to use the default ./backups directory beside the app. '
        'Path is created on first use; must be writable by the server.',
    ),
    'backup.schedule.enabled': (
        False, 'bool', False,
        'Master switch for scheduled automatic backups. When false the '
        'admin Backups page still allows manual create/restore.',
    ),
    'backup.schedule.interval_hours': (
        24, 'int', False,
        'How often a scheduled backup runs, in hours. Minimum 1.',
    ),
    'backup.schedule.include_uploads': (
        True, 'bool', False,
        'Include the uploads tree in scheduled backups. Disable for very '
        'large media libraries that are backed up out-of-band.',
    ),
    'backup.schedule.include_plugins': (
        True, 'bool', False,
        'Include the plugins tree in scheduled backups.',
    ),
    'backup.schedule.retain': (
        14, 'int', False,
        'Number of scheduled backups to keep. Older archives are pruned '
        'automatically after each successful run. 0 disables pruning.',
    ),

    # --- plugin signing ------------------------------------------------------
    'plugin.signing.secret': (
        '', 'string', True,
        'Local HMAC-SHA256 secret used to sign plugin manifests. Auto-'
        'generated on first boot. Treat as sensitive -- anyone with this '
        'secret can mint a valid signature.',
    ),
    'plugin.signing.trust_list': (
        [], 'json', False,
        'JSON list of additional hex-encoded HMAC secrets accepted when '
        'verifying plugin signatures. The local secret is always trusted; '
        'add peer-server secrets here to trust plugins signed elsewhere.',
    ),
    'plugin.signing.require_signed': (
        False, 'bool', False,
        'When true, plugins without a valid signature are refused at the '
        'runner. Default false to preserve back-compat with existing plugins.',
    ),

    # --- proof of play -------------------------------------------------------
    'proof_of_play.enabled': (
        False, 'bool', False,
        'Master switch for Proof-of-Play recording. Off by default; enable '
        'per deployment when audit/compliance requires playback evidence.',
    ),
    'proof_of_play.retention_days': (
        365, 'int', False,
        'How long Proof-of-Play rows are kept before the periodic sweep '
        'deletes them. 0 = keep forever.',
    ),
    'proof_of_play.min_duration_ms': (
        1000, 'int', False,
        'Reports with a smaller duration than this are dropped server-side. ' 
        'Filters out flicker / abandoned slides.',
    ),

    # --- per-tenant policy (stored on Domain, edited via settings UI) ----------
    'tenant.storage_quota_mb': (
        None, 'int', False,
        'Per-tenant media storage quota in MB. Empty = unlimited. '
        'Superadmin only; enforced on upload.',
    ),
}


# -----------------------------------------------------------------------------
# Setting scope policy (who may edit at global vs tenant scope)
# -----------------------------------------------------------------------------
SCOPE_GLOBAL = 'global'
SCOPE_TENANT = 'tenant'
SCOPE_SUPERADMIN_TENANT = 'superadmin_tenant'

# Virtual keys: not stored in system_setting; backed by Domain or similar.
VIRTUAL_DOMAIN_KEYS = frozenset({'tenant.storage_quota_mb'})

_SETTING_SCOPE_OVERRIDES = {
    'tenant.storage_quota_mb': SCOPE_SUPERADMIN_TENANT,
    # Cross-tenant recipient routing — superadmin only at global scope.
    'alerts.user_recipients': SCOPE_GLOBAL,
    'alerts.digest_last_sent': SCOPE_GLOBAL,
    'alerts.email_to': SCOPE_GLOBAL,
}

# Prefixes tenant admins may override for their tenant (SMTP, webhooks, etc.).
TENANT_ADMIN_PREFIXES = ('alerts.',)

# Everything else in BUILTIN_DEFAULTS defaults to global (superadmin, server-wide).
GLOBAL_ONLY_PREFIXES = (
    'security.', 'server.', 'default_timezone', 'job.', 'sse.',
    'heartbeat.', 'upload.', 'cache.', 'disk.', 'ratelimit.',
    'audit.retention.', 'backup.', 'plugin.', 'proof_of_play.',
)


def setting_scope(key):
    """Return SCOPE_GLOBAL, SCOPE_TENANT, or SCOPE_SUPERADMIN_TENANT."""
    if key.startswith('auto.'):
        return SCOPE_GLOBAL
    if key in _SETTING_SCOPE_OVERRIDES:
        return _SETTING_SCOPE_OVERRIDES[key]
    for prefix in TENANT_ADMIN_PREFIXES:
        if key.startswith(prefix):
            return SCOPE_TENANT
    return SCOPE_GLOBAL


def is_virtual_domain_key(key):
    return key in VIRTUAL_DOMAIN_KEYS


def keys_for_scope(scope):
    """Builtin keys classified under one scope (for docs / API policy)."""
    out = []
    for key in BUILTIN_DEFAULTS:
        if setting_scope(key) == scope:
            out.append(key)
    return sorted(out)


def settings_policy_summary():
    """Human-facing policy lists for the settings admin UI."""
    tenant = keys_for_scope(SCOPE_TENANT)
    return {
        'global_only': keys_for_scope(SCOPE_GLOBAL),
        'tenant_editable': tenant,
        'superadmin_tenant_only': keys_for_scope(SCOPE_SUPERADMIN_TENANT),
        'tenant_editable_prefixes': list(TENANT_ADMIN_PREFIXES),
        'global_only_prefixes': list(GLOBAL_ONLY_PREFIXES),
    }


# -----------------------------------------------------------------------------
# Value (de)serialization
# -----------------------------------------------------------------------------
def _decode(raw, vtype):
    if raw is None:
        return None
    if vtype == 'int':
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    if vtype == 'bool':
        return str(raw).lower() in ('1', 'true', 'yes', 'on')
    if vtype == 'json':
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None
    return raw   # string


def _encode(value, vtype):
    if value is None:
        return None
    if vtype == 'json':
        return json.dumps(value)
    if vtype == 'bool':
        return '1' if value else '0'
    return str(value)


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def get(key, domain_id=None, default=None):
    """Return the raw stored value for a single (domain_id, key) pair, or
    None if absent. Does NOT apply precedence -- use effective_value() for that."""
    with bypass_tenant_filter():
        row = (SystemSetting.query
               .filter_by(domain_id=domain_id, key=key)
               .one_or_none())
    if row is None:
        return default
    return _decode(row.value, row.value_type)


def effective_value(key, domain_id=None):
    """Return the value with full precedence: domain override > global >
    builtin default > None."""
    if domain_id is not None:
        v = get(key, domain_id=domain_id, default=_MISSING)
        if v is not _MISSING:
            return v
    v = get(key, domain_id=None, default=_MISSING)
    if v is not _MISSING:
        return v
    spec = BUILTIN_DEFAULTS.get(key)
    if spec is not None:
        return spec[0]
    return None


_MISSING = object()


def set(key, value, domain_id=None, user_id=None, is_auto=False, value_type=None,
        is_sensitive=False, _allow_unknown=False):
    """Insert or update a setting. value_type defaults to BUILTIN_DEFAULTS spec
    if known, else 'string'. Pass _allow_unknown=True to write keys that aren't
    declared in BUILTIN_DEFAULTS (used by auto-detection for auto.* keys)."""
    spec = BUILTIN_DEFAULTS.get(key)
    if spec is None and not _allow_unknown:
        raise KeyError(f'Unknown setting key: {key!r}. Add it to '
                       'settings.BUILTIN_DEFAULTS or pass _allow_unknown=True.')
    if value_type is None:
        value_type = spec[1] if spec else 'string'
    if spec is not None:
        is_sensitive = spec[2]
    encoded = _encode(value, value_type)

    with bypass_tenant_filter():
        row = (SystemSetting.query
               .filter_by(domain_id=domain_id, key=key)
               .one_or_none())
        if row is None:
            row = SystemSetting(
                domain_id=domain_id, key=key, value=encoded,
                value_type=value_type, is_auto=is_auto,
                is_sensitive=is_sensitive,
                updated_by_user_id=user_id,
            )
            db.session.add(row)
        else:
            row.value = encoded
            row.value_type = value_type
            row.is_auto = is_auto
            row.is_sensitive = is_sensitive
            row.updated_by_user_id = user_id
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            raise
    return row


def delete(key, domain_id=None):
    with bypass_tenant_filter():
        row = (SystemSetting.query
               .filter_by(domain_id=domain_id, key=key)
               .one_or_none())
        if row is not None:
            db.session.delete(row)
            db.session.commit()


def all_for_domain(domain_id=None, include_auto=True):
    """Return a dict of {key: decoded_value} for the given scope."""
    with bypass_tenant_filter():
        q = SystemSetting.query.filter_by(domain_id=domain_id)
        if not include_auto:
            q = q.filter_by(is_auto=False)
        rows = q.all()
    return {r.key: _decode(r.value, r.value_type) for r in rows}
