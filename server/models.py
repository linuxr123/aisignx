"""
AISignX models - Phase 1 schema.

Clean break from the v1 single-tenant schema. Every tenant-scoped table now
carries domain_id. New tables: Domain, Role, Permission, RolePermission,
UserDomainRole, AuditLog, SystemSetting, BackgroundJob.

Design notes
------------
* Multi-tenant by default. The TenantQuery (in tenant_filter.py) auto-injects
  WHERE domain_id = current_domain_id() on tenant-scoped models. Every
  tenant model below uses the TenantModel mixin to opt in.
* Roles + permissions are global tables; assignment to a (user, domain) pair
  lives in UserDomainRole.
* Variants on Media use a JSON column. Images get auto-generated variants in
  Phase 2; videos only have variants the admin uploads explicitly.
* All file paths are RELATIVE to the storage root (UPLOAD_FOLDER) and are
  always domain-prefixed: <domain_slug>/yyyy/mm/<uuid>.<ext>.
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import Index, UniqueConstraint
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash


db = SQLAlchemy()


# -----------------------------------------------------------------------------
# Tenant query class
# -----------------------------------------------------------------------------
def _tenant_query():
    """Lazy import of TenantQuery so models.py has no import-time dependency
    on tenant_filter.py (which depends on this module's db)."""
    from tenant_filter import TenantQuery
    return TenantQuery


class TenantModel:
    """Mixin marking a model as tenant-scoped. Patches in TenantQuery as the
    model's query_class once SQLAlchemy has finished mapper configuration."""

    @classmethod
    def __declare_last__(cls):
        try:
            cls.query_class = _tenant_query()
        except Exception:
            # If the import fails (e.g. during alembic offline mode), the
            # model still works -- it just doesn't get tenant-filtered.
            pass


# -----------------------------------------------------------------------------
# Association tables
# -----------------------------------------------------------------------------
display_schedule = db.Table(
    'display_schedule',
    db.Column('display_id',  db.Integer, db.ForeignKey('display.id'),  primary_key=True),
    db.Column('schedule_id', db.Integer, db.ForeignKey('schedule.id'), primary_key=True),
)


# -----------------------------------------------------------------------------
# Domain (tenant)
# -----------------------------------------------------------------------------
class Domain(db.Model):
    """A tenant. Every tenant-scoped row carries a domain_id pointing here."""
    __tablename__ = 'domain'

    id           = db.Column(db.Integer, primary_key=True)
    name         = db.Column(db.String(120), nullable=False, unique=True)
    slug         = db.Column(db.String(64),  nullable=False, unique=True, index=True)
    description  = db.Column(db.Text)

    # Branding (Phase 1: logo + primary color only).
    branding_logo_path     = db.Column(db.String(255), nullable=True)
    branding_primary_color = db.Column(db.String(16),  default='#0d6efd')

    # Storage policy. Quotas are tracked but NOT enforced in Phase 1.
    storage_quota_bytes    = db.Column(db.BigInteger, nullable=True)
    storage_used_bytes     = db.Column(db.BigInteger, default=0, nullable=False)

    # Per-domain feature flags (e.g. {"multi_zone_layouts": false}).
    features               = db.Column(db.JSON, default=dict, nullable=False)

    # Per-domain default tz (falls back to system_settings.default_timezone).
    default_timezone       = db.Column(db.String(64), nullable=True)

    # Retention policy is informational in Phase 1 (no enforcement).
    retention_policy       = db.Column(db.JSON, nullable=True)

    is_active    = db.Column(db.Boolean, default=True, nullable=False)

    # ── Device enrollment (proof-of-invitation) ──────────────────────────
    # Native/browser clients must present an enrollment_code that resolves
    # to exactly one Domain before /api/register will create a pending row.
    # Without this, any device on the network could spam every tenant's
    # pending queue. The code is rotatable and revocable from the Domain
    # admin UI; nullable means "enrollment closed".
    enrollment_code             = db.Column(db.String(40), unique=True,
                                            nullable=True, index=True)
    enrollment_code_expires_at  = db.Column(db.DateTime, nullable=True)
    enrollment_enabled          = db.Column(db.Boolean, default=True,
                                            nullable=False)

    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)

    def has_feature(self, key):
        return bool((self.features or {}).get(key, False))

    def enrollment_is_open(self):
        """True iff this domain currently accepts new device registrations.
        Requires enabled flag, a non-empty code, and (if set) a future expiry."""
        if not self.enrollment_enabled:
            return False
        if not self.enrollment_code:
            return False
        if self.enrollment_code_expires_at and \
           self.enrollment_code_expires_at <= datetime.utcnow():
            return False
        return True

    def to_dict(self):
        return {
            'id':                     self.id,
            'name':                   self.name,
            'slug':                   self.slug,
            'description':            self.description,
            'branding_logo_path':     self.branding_logo_path,
            'branding_primary_color': self.branding_primary_color,
            'storage_quota_bytes':    self.storage_quota_bytes,
            'storage_used_bytes':     self.storage_used_bytes,
            'features':               self.features or {},
            'default_timezone':       self.default_timezone,
            'is_active':              self.is_active,
            'created_at':             self.created_at.isoformat() if self.created_at else None,
        }


# -----------------------------------------------------------------------------
# Permissions + Roles (RBAC)
# -----------------------------------------------------------------------------
class Permission(db.Model):
    __tablename__ = 'permission'

    id          = db.Column(db.Integer, primary_key=True)
    key         = db.Column(db.String(64), nullable=False, unique=True, index=True)
    description = db.Column(db.String(255))
    is_system   = db.Column(db.Boolean, default=False, nullable=False)


role_permission = db.Table(
    'role_permission',
    db.Column('role_id',       db.Integer, db.ForeignKey('role.id',       ondelete='CASCADE'),
              primary_key=True),
    db.Column('permission_id', db.Integer, db.ForeignKey('permission.id', ondelete='CASCADE'),
              primary_key=True),
)


class Role(db.Model):
    """A named bundle of permissions. May be a system role (global, immutable,
    domain_id NULL) or a custom domain role (scoped + mutable)."""
    __tablename__ = 'role'

    id          = db.Column(db.Integer, primary_key=True)
    domain_id   = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                            nullable=True, index=True)
    name        = db.Column(db.String(80),  nullable=False)
    description = db.Column(db.String(255))
    is_system   = db.Column(db.Boolean, default=False, nullable=False)

    domain      = db.relationship('Domain')
    permissions = db.relationship('Permission', secondary=role_permission,
                                   backref='roles', lazy='joined')

    __table_args__ = (
        UniqueConstraint('domain_id', 'name', name='uq_role_domain_name'),
    )


class UserDomainRole(db.Model):
    """Assigns a user a role within a specific domain."""
    __tablename__ = 'user_domain_role'

    id          = db.Column(db.Integer, primary_key=True)
    user_id     = db.Column(db.Integer, db.ForeignKey('user.id',   ondelete='CASCADE'),
                            nullable=False, index=True)
    domain_id   = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                            nullable=False, index=True)
    role_id     = db.Column(db.Integer, db.ForeignKey('role.id',   ondelete='CASCADE'),
                            nullable=False, index=True)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    user        = db.relationship('User',   backref='domain_roles')
    domain      = db.relationship('Domain', backref='user_roles')
    role        = db.relationship('Role')

    __table_args__ = (
        UniqueConstraint('user_id', 'domain_id', 'role_id', name='uq_udr'),
    )


# -----------------------------------------------------------------------------
# User
# -----------------------------------------------------------------------------
class User(UserMixin, db.Model):
    __tablename__ = 'user'

    id            = db.Column(db.Integer, primary_key=True)
    # Login identity is scoped by home_domain_id (NULL = system/superadmin account).
    username      = db.Column(db.String(64), index=True, nullable=False)
    email         = db.Column(db.String(120), index=True, nullable=False)
    password_hash = db.Column(db.String(256))
    active        = db.Column(db.Boolean, default=True, nullable=False)
    last_login    = db.Column(db.DateTime)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = db.Column(db.DateTime, default=datetime.utcnow,
                                onupdate=datetime.utcnow, nullable=False)

    # Tenant this account belongs to for login (duplicate usernames allowed
    # across different tenants). NULL for cross-tenant superadmin accounts.
    home_domain_id = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                               nullable=True, index=True)

    # is_superadmin grants the special "system" superuser role across all
    # domains. Used to bootstrap the first user and rare break-glass paths.
    is_superadmin = db.Column(db.Boolean, default=False, nullable=False)

    # Service accounts authenticate only via API tokens (no web login).
    is_service_account = db.Column(db.Boolean, default=False, nullable=False)

    home_domain   = db.relationship('Domain', foreign_keys=[home_domain_id])

    __table_args__ = (
        UniqueConstraint('home_domain_id', 'username', name='uq_user_tenant_username'),
        UniqueConstraint('home_domain_id', 'email', name='uq_user_tenant_email'),
    )

    def __repr__(self):
        return f'<User {self.username}>'

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash or '', password)

    @property
    def is_active(self):
        return self.active

    @property
    def is_admin(self):
        """Backward-compat shim. Returns True if the user is a superadmin OR
        holds 'domain.admin' in the active tenant. Templates and legacy code
        used `current_user.is_admin` as the "show admin UI" check; that
        meaning is preserved here so we don't have to rewrite every template.

        New code should call permissions.has_permission(user, 'specific.key')
        instead of relying on this catch-all."""
        if self.is_superadmin:
            return True
        try:
            from permissions import has_permission
            return has_permission(self, 'domain.admin')
        except Exception:
            return False

    def domains(self):
        """Distinct list of Domain objects this user has any role in."""
        seen, out = set(), []
        for udr in self.domain_roles:
            if udr.domain_id not in seen:
                seen.add(udr.domain_id)
                out.append(udr.domain)
        return out

    def to_dict(self):
        return {
            'id':            self.id,
            'username':      self.username,
            'email':         self.email,
            'home_domain_id': self.home_domain_id,
            'home_domain_name': self.home_domain.name if self.home_domain else None,
            'home_domain_slug': self.home_domain.slug if self.home_domain else None,
            'is_superadmin': self.is_superadmin,
            'is_service_account': self.is_service_account,
            'active':        self.active,
            'last_login':    self.last_login.isoformat() if self.last_login else None,
            'created_at':    self.created_at.isoformat() if self.created_at else None,
            'domain_count':  len({udr.domain_id for udr in self.domain_roles}),
        }


# -----------------------------------------------------------------------------
# Audit log
# -----------------------------------------------------------------------------
class AuditLog(db.Model):
    """Append-only record of privileged actions. Written by audit() in audit.py."""
    __tablename__ = 'audit_log'

    id                  = db.Column(db.Integer, primary_key=True)
    timestamp           = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    domain_id           = db.Column(db.Integer, db.ForeignKey('domain.id'), nullable=True, index=True)
    actor_user_id       = db.Column(db.Integer, db.ForeignKey('user.id'),  nullable=True, index=True)
    actor_api_token_id  = db.Column(db.Integer, db.ForeignKey('api_token.id'), nullable=True)

    action       = db.Column(db.String(80), nullable=False, index=True)
    target_type  = db.Column(db.String(40), nullable=True)
    target_id    = db.Column(db.String(64), nullable=True)
    payload      = db.Column(db.JSON, nullable=True)

    ip_address   = db.Column(db.String(45))
    user_agent   = db.Column(db.String(255))

    actor_user   = db.relationship('User')
    domain       = db.relationship('Domain')

    __table_args__ = (
        Index('ix_audit_domain_ts', 'domain_id', 'timestamp'),
        Index('ix_audit_user_ts',   'actor_user_id', 'timestamp'),
        Index('ix_audit_action_ts', 'action', 'timestamp'),
    )


# -----------------------------------------------------------------------------
# System settings + background jobs
# -----------------------------------------------------------------------------
class SystemSetting(db.Model):
    """Versioned key/value store. Global keys have domain_id = NULL; per-domain
    overrides have domain_id set."""
    __tablename__ = 'system_setting'

    id              = db.Column(db.Integer, primary_key=True)
    domain_id       = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                                  nullable=True, index=True)
    key             = db.Column(db.String(120), nullable=False, index=True)
    value           = db.Column(db.Text, nullable=True)        # serialized per value_type
    value_type      = db.Column(db.String(16), nullable=False, default='string')  # string|int|bool|json
    is_auto         = db.Column(db.Boolean, default=False, nullable=False)
    is_sensitive    = db.Column(db.Boolean, default=False, nullable=False)
    updated_at      = db.Column(db.DateTime, default=datetime.utcnow,
                                  onupdate=datetime.utcnow, nullable=False)
    updated_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    __table_args__ = (
        UniqueConstraint('domain_id', 'key', name='uq_setting_domain_key'),
    )


class BackgroundJob(db.Model):
    __tablename__ = 'background_job'

    id            = db.Column(db.Integer, primary_key=True)
    kind          = db.Column(db.String(64), nullable=False, index=True)
    status        = db.Column(db.String(16), nullable=False, default='pending', index=True)
    # pending | running | done | failed | cancelled

    domain_id     = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                                nullable=True, index=True)

    payload       = db.Column(db.JSON,  nullable=True)
    result        = db.Column(db.JSON,  nullable=True)
    last_error    = db.Column(db.Text,  nullable=True)

    attempts      = db.Column(db.Integer, default=0,  nullable=False)
    max_attempts  = db.Column(db.Integer, default=3,  nullable=False)

    worker_id     = db.Column(db.String(120), nullable=True)
    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    started_at    = db.Column(db.DateTime, nullable=True)
    finished_at   = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        Index('ix_job_status_created', 'status', 'created_at'),
    )


# -----------------------------------------------------------------------------
# API token (now domain-scoped)
# -----------------------------------------------------------------------------
class ApiToken(db.Model, TenantModel):
    __tablename__ = 'api_token'

    id            = db.Column(db.Integer, primary_key=True)
    domain_id     = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                                nullable=False, index=True)
    user_id       = db.Column(db.Integer, db.ForeignKey('user.id'),  nullable=False)
    token_hash    = db.Column(db.String(64), nullable=False, unique=True, index=True)
    name          = db.Column(db.String(120))
    scopes        = db.Column(db.String(500))    # comma-separated permission keys

    created_at    = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_used_at  = db.Column(db.DateTime)
    expires_at    = db.Column(db.DateTime, nullable=True)
    revoked       = db.Column(db.Boolean, default=False, nullable=False)

    # OAuth 2.0 fields (Phase 3 will populate). Nullable in P1.
    oauth_client_id     = db.Column(db.String(64), nullable=True, index=True)
    oauth_client_secret = db.Column(db.String(256), nullable=True)

    # Optional restriction to a single media item (legacy automation case).
    media_id      = db.Column(db.Integer, db.ForeignKey('media.id'), nullable=True)

    domain        = db.relationship('Domain')
    media         = db.relationship('Media')
    user          = db.relationship('User', backref='api_tokens')

    def has_scope(self, scope):
        if not self.scopes:
            return True
        return scope in [s.strip() for s in self.scopes.split(',') if s.strip()]


# -----------------------------------------------------------------------------
# Display group + display
# -----------------------------------------------------------------------------
class DisplayGroup(db.Model, TenantModel):
    __tablename__ = 'display_group'

    id           = db.Column(db.Integer, primary_key=True)
    domain_id    = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    name         = db.Column(db.String(120), nullable=False)
    description  = db.Column(db.String(255))

    # Phase 3: hierarchical groups. parent_id references another group in
    # the SAME tenant (the API layer enforces the same-tenant invariant
    # because SQLAlchemy can't express it as a constraint alone).
    # ondelete=SET NULL means deleting a parent reparents children to the
    # root rather than cascading them away.
    parent_id    = db.Column(db.Integer,
                              db.ForeignKey('display_group.id', ondelete='SET NULL'),
                              nullable=True, index=True)

    # Phase 4: synchronized-start playback.
    sync_playback = db.Column(db.Boolean, default=False, nullable=False)

    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)

    domain       = db.relationship('Domain')
    displays     = db.relationship('Display', backref='group', lazy=True)
    parent       = db.relationship('DisplayGroup', remote_side=[id],
                                    backref=db.backref('children', lazy='dynamic'))

    def to_dict(self):
        return {
            'id':            self.id,
            'domain_id':     self.domain_id,
            'name':          self.name,
            'description':   self.description,
            'parent_id':     self.parent_id,
            'sync_playback': self.sync_playback,
            'display_count': len(self.displays) if self.displays else 0,
            'created_at':    self.created_at.isoformat() if self.created_at else None,
        }


class Display(db.Model, TenantModel):
    __tablename__ = 'display'

    id           = db.Column(db.Integer, primary_key=True)
    domain_id    = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    name         = db.Column(db.String(120), nullable=False)
    device_id    = db.Column(db.String(100), nullable=False, unique=True, index=True)
    api_key      = db.Column(db.String(100), nullable=False, index=True)
    location     = db.Column(db.String(120))
    description  = db.Column(db.Text)

    status       = db.Column(db.String(20), default='offline')
    last_ping    = db.Column(db.DateTime)
    ip_address   = db.Column(db.String(45))

    # Client build identifier. Reported by the player on every ping
    # (Electron clients send their app version; browser clients send a
    # 'browser' fallback). Surfaced on the displays page so admins can
    # spot version drift and confirm rollouts.
    app_version  = db.Column(db.String(40))

    # Reported by client on first connect via /api/v1/displays/<token>/capabilities.
    # Shape: {"max_video_height": 1080, "codecs": ["h264","vp9"], "max_image_dim": 4096,
    #         "screen_w": 1920, "screen_h": 1080}
    capabilities = db.Column(db.JSON, default=dict, nullable=False)

    # Display preferences (kept flat for backwards compatibility).
    resolution_x        = db.Column(db.Integer, nullable=True)
    resolution_y        = db.Column(db.Integer, nullable=True)
    orientation         = db.Column(db.String(20), default='landscape')
    aspect_mode         = db.Column(db.String(20), default='fit')
    show_media_buttons  = db.Column(db.Boolean, default=False, nullable=False)
    allow_input         = db.Column(db.Boolean, default=False, nullable=False)
    show_offline_banner = db.Column(db.Boolean, default=True,  nullable=False)
    # When true, native clients (Android / Electron) treat updates as fully
    # automatic: download + install + restart without prompts, regardless of
    # the per-device setup "update mode" (still no-op when setup is manual-only
    # for backwards safety — server flag upgrades prompt → silent auto).
    auto_update_client  = db.Column(db.Boolean, default=False, nullable=False)
    # Allows a display to share a group's schedule/playlist without joining
    # that group's synchronized playback timing.
    sync_playback_opt_out = db.Column(db.Boolean, default=False, nullable=False)
    # Opt-in client diagnostic logging. When True the player captures
    # console output, sync events, and errors and streams them to the
    # server via /api/display/<token>/diagnostics. Off by default so we
    # never collect data without explicit admin consent per display.
    diagnostics_enabled = db.Column(db.Boolean, default=False, nullable=False)
    unlock_pin          = db.Column(db.String(8), default='')
    # Master playback volume 0-100. Applied to every <video> element on
    # the player; 0 also forces the muted attribute so autoplay survives
    # browsers that block audible autoplay.
    volume              = db.Column(db.Integer, default=100, nullable=False)

    # Heartbeat-batched playback state. Updated <= every 60s by the player.
    current_content     = db.Column(db.String(200))
    current_playlist_item_id = db.Column(db.Integer, nullable=True)

    created_by_user_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at          = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at          = db.Column(db.DateTime, default=datetime.utcnow,
                                       onupdate=datetime.utcnow, nullable=False)

    group_id            = db.Column(db.Integer, db.ForeignKey('display_group.id'),
                                       nullable=True, index=True)

    domain              = db.relationship('Domain')
    schedules           = db.relationship('Schedule',
                                            secondary=display_schedule,
                                            backref=db.backref('displays', lazy='dynamic'),
                                            lazy='dynamic')

    def to_dict(self):
        return {
            'id':                  self.id,
            'domain_id':           self.domain_id,
            'name':                self.name,
            'device_id':           self.device_id,
            'api_key':             self.api_key,
            'location':            self.location,
            'description':         self.description,
            'status':              self.status,
            'last_ping':           self.last_ping.isoformat() if self.last_ping else None,
            'ip_address':          self.ip_address,
            'capabilities':        self.capabilities or {},
            'resolution_x':        self.resolution_x,
            'resolution_y':        self.resolution_y,
            'orientation':         self.orientation,
            'aspect_mode':         self.aspect_mode,
            'show_media_buttons':  self.show_media_buttons,
            'allow_input':         self.allow_input,
            'show_offline_banner': self.show_offline_banner,
            'auto_update_client':  bool(self.auto_update_client),
            'sync_playback_opt_out': bool(getattr(self, 'sync_playback_opt_out', False)),
            'diagnostics_enabled': bool(getattr(self, 'diagnostics_enabled', False)),
            'volume':              int(self.volume if self.volume is not None else 100),
            'group_id':            self.group_id,
            'group':               self.group.to_dict() if self.group else None,
            'created_at':          self.created_at.isoformat() if self.created_at else None,
        }


# -----------------------------------------------------------------------------
# Pending display registration
# -----------------------------------------------------------------------------
class PendingDisplay(db.Model):
    """Pre-registration entry. Domain is captured at registration time so
    only that tenant's admins can see/approve the device. NULL means the
    device did not declare a tenant — visible only to superadmins."""
    __tablename__ = 'pending_display'

    id           = db.Column(db.Integer, primary_key=True)
    device_id    = db.Column(db.String(100), nullable=False, unique=True, index=True)
    friendly_name = db.Column(db.String(120))
    hostname     = db.Column(db.String(120))
    os           = db.Column(db.String(50))
    resolution   = db.Column(db.String(20))
    app_version  = db.Column(db.String(20))
    ip_address   = db.Column(db.String(45))
    requested_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    status       = db.Column(db.String(20), default='pending')   # pending | approved | declined
    display_id   = db.Column(db.Integer, db.ForeignKey('display.id'), nullable=True)
    # Domain the device wants to join (claimed at registration time, e.g.
    # via the per-tenant /request-access?domain=<slug> URL or an explicit
    # domain_slug field in the native registration payload). Locks visibility
    # in the admin pending list and prevents cross-tenant approval hijacks.
    domain_id          = db.Column(db.Integer, db.ForeignKey('domain.id'), nullable=True, index=True)
    approved_domain_id = db.Column(db.Integer, db.ForeignKey('domain.id'), nullable=True)
    # Audit trail: which enrollment_code value the device presented when it
    # registered. Lets admins mass-decline a leaked code without affecting
    # devices enrolled under a different one.
    enrollment_code_used = db.Column(db.String(40), nullable=True, index=True)
    user_agent           = db.Column(db.String(255), nullable=True)

    display      = db.relationship('Display')

    def to_dict(self):
        return {
            'id':            self.id,
            'device_id':     self.device_id,
            'friendly_name': self.friendly_name,
            'hostname':      self.hostname,
            'os':             self.os,
            'resolution':    self.resolution,
            'app_version':   self.app_version,
            'ip_address':    self.ip_address,
            'requested_at':  self.requested_at.isoformat() if self.requested_at else None,
            'status':        self.status,
            'display_id':    self.display_id,
            'domain_id':         self.domain_id,
            'approved_domain_id': self.approved_domain_id,
            'enrollment_code_used': self.enrollment_code_used,
            'user_agent':         self.user_agent,
        }


# -----------------------------------------------------------------------------
# Display diagnostic log entries
#
# Opt-in per-display debug log. When Display.diagnostics_enabled is True, the
# player streams captured console output, sync events, errors and ping
# failures here in small batches. The table is auto-pruned per display so
# leaving diagnostics on permanently can't blow up the database.
# -----------------------------------------------------------------------------
class DisplayDiagnostic(db.Model):
    __tablename__ = 'display_diagnostic'

    id          = db.Column(db.Integer, primary_key=True)
    display_id  = db.Column(db.Integer, db.ForeignKey('display.id', ondelete='CASCADE'),
                            nullable=False, index=True)
    # When the server received the entry. Used for ordering and TTL purges.
    received_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    # When the client recorded the entry (its OWN clock at the time -- we
    # also send `client_server_now` so we can compare against server time).
    client_ts   = db.Column(db.DateTime, nullable=True)
    # 'info' | 'warn' | 'error' | 'sync' | 'net' | 'play'
    level       = db.Column(db.String(16), nullable=False, default='info', index=True)
    source      = db.Column(db.String(64), nullable=True)   # 'console' | 'onerror' | 'sync' | ...
    message     = db.Column(db.Text,   nullable=False, default='')
    meta        = db.Column(db.JSON,   nullable=True)

    display     = db.relationship('Display')

    def to_dict(self):
        return {
            'id':          self.id,
            'display_id':  self.display_id,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'client_ts':   self.client_ts.isoformat() if self.client_ts else None,
            'level':       self.level,
            'source':      self.source,
            'message':     self.message,
            'meta':        self.meta,
        }


# -----------------------------------------------------------------------------
# Emergency broadcasts (now domain-scoped)
# -----------------------------------------------------------------------------
class EmergencyBroadcast(db.Model, TenantModel):
    __tablename__ = 'emergency_broadcast'

    LEVEL_INFO     = 'info'
    LEVEL_WARNING  = 'warning'
    LEVEL_CRITICAL = 'critical'

    id           = db.Column(db.Integer, primary_key=True)
    domain_id    = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    title        = db.Column(db.String(200), nullable=False)
    message      = db.Column(db.Text)
    level        = db.Column(db.String(20), default='critical')
    background_color = db.Column(db.String(20), default='#cc0000')
    text_color       = db.Column(db.String(20), default='#ffffff')
    target       = db.Column(db.String(50), default='all')
    is_active    = db.Column(db.Boolean, default=True, nullable=False)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    cleared_at   = db.Column(db.DateTime, nullable=True)
    created_by   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    cleared_by   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    domain       = db.relationship('Domain')
    creator      = db.relationship('User', foreign_keys=[created_by])
    clearer      = db.relationship('User', foreign_keys=[cleared_by])

    def is_live(self):
        return bool(self.is_active)

    def applies_to(self, display):
        if not self.is_live():
            return False
        if getattr(display, 'domain_id', None) != self.domain_id:
            return False
        if self.target == 'all':
            return True
        if self.target == f'display:{display.id}':
            return True
        if display.group_id and self.target == f'group:{display.group_id}':
            return True
        return False

    def to_dict(self):
        return {
            'id':               self.id,
            'domain_id':        self.domain_id,
            'title':            self.title,
            'message':          self.message,
            'level':            self.level or 'critical',
            'background_color': self.background_color,
            'text_color':       self.text_color,
            'target':           self.target,
            'is_active':        self.is_active,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
            'cleared_at':       self.cleared_at.isoformat() if self.cleared_at else None,
            'created_by':       self.creator.username if self.creator else None,
            'cleared_by':       self.clearer.username if self.clearer else None,
        }


class EmergencyTemplate(db.Model, TenantModel):
    __tablename__ = 'emergency_template'

    id           = db.Column(db.Integer, primary_key=True)
    domain_id    = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    name         = db.Column(db.String(120), nullable=False)
    title        = db.Column(db.String(200), nullable=False)
    message      = db.Column(db.Text)
    level        = db.Column(db.String(20), default='critical')
    background_color = db.Column(db.String(20), default='#b71c1c')
    text_color       = db.Column(db.String(20), default='#ffffff')
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)

    domain       = db.relationship('Domain')
    creator      = db.relationship('User', foreign_keys=[created_by])

    def to_dict(self):
        return {
            'id':               self.id,
            'domain_id':        self.domain_id,
            'name':             self.name,
            'title':            self.title,
            'message':          self.message,
            'level':            self.level or 'critical',
            'background_color': self.background_color,
            'text_color':       self.text_color,
            'created_at':       self.created_at.isoformat() if self.created_at else None,
            'created_by':       self.creator.username if self.creator else None,
        }


# -----------------------------------------------------------------------------
# Media (variants + checksum + capabilities-aware metadata)
# -----------------------------------------------------------------------------
class Media(db.Model, TenantModel):
    __tablename__ = 'media'

    id           = db.Column(db.Integer, primary_key=True)
    domain_id    = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    name         = db.Column(db.String(120), nullable=False)
    filename     = db.Column(db.String(255), nullable=False)
    file_path    = db.Column(db.String(255), nullable=False)
    media_type   = db.Column(db.String(20),  nullable=False)
    mime_type    = db.Column(db.String(80))
    duration     = db.Column(db.Integer, default=10)
    description  = db.Column(db.Text)
    file_size    = db.Column(db.BigInteger)
    meta_data    = db.Column(db.JSON)

    # Detected metadata (filled by ffprobe / Pillow on upload).
    width        = db.Column(db.Integer, nullable=True)
    height       = db.Column(db.Integer, nullable=True)
    duration_seconds = db.Column(db.Float, nullable=True)
    codec        = db.Column(db.String(40), nullable=True)
    bitrate_bps  = db.Column(db.BigInteger, nullable=True)

    # SHA-256 hex digest of the original file.
    checksum_sha256 = db.Column(db.String(64), nullable=True, index=True)

    # Variants. Each value:
    #   {"file_path": "<rel>", "checksum": "<sha256>", "size": 12345,
    #    "width": 3840, "height": 2160, "format": "webp"}
    # Image keys (Phase 2): "4k" | "1080p" | "720p" | "thumb"
    # Video keys (Phase 1, manual): "4k" | "1080p" | "720p"
    variants     = db.Column(db.JSON, default=dict, nullable=False)
    original_variant_key = db.Column(db.String(16), nullable=True)

    thumbnail_path = db.Column(db.String(255))
    external_id    = db.Column(db.String(128), unique=True, index=True, nullable=True)

    # Status of the thumbnail generation pipeline:
    #   'ok'         – a usable thumbnail exists on disk (default)
    #   'pending'    – generation queued or in progress
    #   'failed'     – last generation attempt errored; placeholder shown
    # Used by the media list UI to surface a badge so operators can spot
    # broken thumbnails and trigger a regenerate without leaving the page.
    thumbnail_status = db.Column(db.String(20), default='ok', nullable=True)
    thumbnail_generated_at = db.Column(db.DateTime, nullable=True)

    # Comma-separated tag list (e.g. "lobby,promo,en"). Stored as a single
    # text column to keep filtering cheap and avoid a join table; the
    # media UI provides chip-based add/remove, and the API normalises
    # input (lowercased, deduped, stripped).
    tags         = db.Column(db.String(500), default='', nullable=True)

    # Logical folder path, '/'-separated, no leading/trailing slash.
    # NULL or '' means "uncategorised" (root). Folders are purely a
    # metadata grouping for the Media library UI; they do NOT correspond
    # to a directory on disk -- the actual file location is `file_path`.
    # Indexed because the library view filters by it on every page load.
    folder       = db.Column(db.String(255), default='', nullable=True, index=True)

    # Per-file default for video audio playback. True (default) lets the
    # player emit sound for this video; False mutes it everywhere unless
    # a playlist or per-item override flips it back. Only meaningful for
    # ``media_type == 'video'``; ignored for other types.
    audio_enabled = db.Column(db.Boolean, default=True, nullable=False)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)

    domain       = db.relationship('Domain')
    playlist_items = db.relationship('PlaylistItem', backref='media',
                                       cascade='all, delete-orphan')

    def to_dict(self):
        result = {
            'id':              self.id,
            'domain_id':       self.domain_id,
            'name':            self.name,
            'filename':        self.filename,
            'file_path':       self.file_path,
            'media_type':      self.media_type,
            'mime_type':       self.mime_type,
            'duration':        self.duration,
            'description':     self.description,
            'file_size':       self.file_size,
            'width':           self.width,
            'height':          self.height,
            'duration_seconds': self.duration_seconds,
            'codec':           self.codec,
            'bitrate_bps':     self.bitrate_bps,
            'checksum_sha256': self.checksum_sha256,
            'variants':        self.variants or {},
            'original_variant_key': self.original_variant_key,
            'thumbnail_path':  self.thumbnail_path,
            'thumbnail_url':   None,
            'thumbnail_status': self.thumbnail_status or 'ok',
            'thumbnail_generated_at': self.thumbnail_generated_at.isoformat() if self.thumbnail_generated_at else None,
            'tags':            [t for t in (self.tags or '').split(',') if t],
            'folder':          self.folder or '',
            'audio_enabled':   bool(self.audio_enabled if self.audio_enabled is not None else True),
            'created_at':      self.created_at.isoformat() if self.created_at else None,
        }
        if self.media_type == 'webpage':
            result['url'] = self.file_path
            if self.meta_data:
                result['refresh_interval'] = self.meta_data.get('refresh_interval', 0)
                result['scrolling']        = self.meta_data.get('scrolling', True)
        return result

    def best_variant_for(self, capabilities):
        """Return the variant dict matching the display's capabilities, or
        None to fall back to file_path. Used by media_storage.url(); refined
        in Phase 2 once the image variant pipeline ships."""
        v = self.variants or {}
        if not v:
            return None
        cap = capabilities or {}
        if self.media_type == 'video':
            limit = int(cap.get('max_video_height', 2160) or 2160)
        else:
            limit = int(cap.get('max_image_dim', 4096) or 4096)
        candidates = sorted(
            ((k, vv) for k, vv in v.items() if vv.get('height')),
            key=lambda kv: kv[1]['height'], reverse=True,
        )
        for _, vv in candidates:
            if vv['height'] <= limit:
                return vv
        if candidates:
            return candidates[-1][1]
        return None


# -----------------------------------------------------------------------------
# Playlist + items
# -----------------------------------------------------------------------------
class Playlist(db.Model, TenantModel):
    __tablename__ = 'playlist'

    id           = db.Column(db.Integer, primary_key=True)
    domain_id    = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    name         = db.Column(db.String(120), nullable=False)
    description  = db.Column(db.Text)

    # Default transition applied to items that don't override their own.
    # Values: 'cut' (no transition / "none"), 'fade', 'crossfade', 'wipe',
    # or 'random' (player picks per slide). Per-item ``transition`` always
    # wins. NULL is treated as 'cut' for back-compat.
    default_transition = db.Column(db.String(16), nullable=True)

    # Phase 2: when this playlist's resolution fails, the player falls back
    # to this playlist. Dormant in Phase 1.
    fallback_playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.id'),
                                       nullable=True)

    # Smart playlists: when ``smart_rules`` is set (JSON text), the playlist's
    # contents are computed dynamically from the Media library at resolve
    # time instead of being managed via PlaylistItem rows. Rule schema (v1):
    #   {"all_tags": [...], "any_tags": [...], "exclude_tags": [...],
    #    "media_types": ["image","video","webpage"], "name_contains": "..."}
    # ``smart_order`` is one of: 'newest', 'oldest', 'name', 'random'
    # (defaults to 'newest'). ``smart_limit`` caps the number of items
    # (defaults to 50, hard max 500).
    smart_rules  = db.Column(db.Text, nullable=True)
    smart_order  = db.Column(db.String(32), nullable=True)
    smart_limit  = db.Column(db.Integer, nullable=True)

    # Playlist-wide override for video audio: 'inherit' uses the per-media
    # ``audio_enabled`` flag; 'on' forces audio on for every video in this
    # playlist; 'off' force-mutes every video. Per-item ``mute_audio`` still
    # wins so admins can silence one slide in an otherwise-loud playlist.
    video_audio_default = db.Column(db.String(10), default='inherit', nullable=False)

    # Comma-separated whitelist of transition names the player picks from
    # when default_transition='random'. Empty/NULL means "use the built-in
    # default pool" (every animated transition the player knows).
    random_transitions = db.Column(db.String(255), nullable=True, default='')

    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)

    domain       = db.relationship('Domain')
    items        = db.relationship('PlaylistItem', backref='playlist',
                                     order_by='PlaylistItem.position',
                                     cascade='all, delete-orphan',
                                     lazy=True)
    schedules    = db.relationship('Schedule', backref='playlist', lazy=True)

    def to_dict(self):
        return {
            'id':           self.id,
            'domain_id':    self.domain_id,
            'name':         self.name,
            'description':  self.description,
            'default_transition': self.default_transition or 'cut',
            'fallback_playlist_id': self.fallback_playlist_id,
            'item_count':   len(self.resolved_items()),
            'is_smart':     bool(self.smart_rules),
            'smart_rules':  self._smart_rules_dict(),
            'smart_order':  self.smart_order or ('newest' if self.smart_rules else None),
            'smart_limit':  self.smart_limit if self.smart_rules else None,
            'video_audio_default': self.video_audio_default or 'inherit',
            'random_transitions': [s for s in (self.random_transitions or '').split(',') if s],
            'created_at':   self.created_at.isoformat() if self.created_at else None,
        }

    # ------------------------------------------------------------------
    # Smart playlist helpers
    # ------------------------------------------------------------------
    def _smart_rules_dict(self):
        """Return ``smart_rules`` parsed as a dict, or None if not smart /
        invalid. Errors here are silent so a corrupt rule blob can't break
        the playlist editor; the resolver returns an empty list instead."""
        if not self.smart_rules:
            return None
        try:
            import json as _json
            v = _json.loads(self.smart_rules)
            return v if isinstance(v, dict) else None
        except Exception:
            return None

    def resolved_items(self):
        """Return the items the player should render. For a normal playlist
        this is just the persisted ``items`` list. For a smart playlist the
        list is computed live from the Media library via
        ``smart_playlists.resolve_smart_items``."""
        if not self.smart_rules:
            return self.items
        try:
            from smart_playlists import resolve_smart_items
            return resolve_smart_items(self)
        except Exception as e:
            from logging_config import logger
            logger.warning(f'smart playlist {self.id} resolve failed: {e}')
            return []


class PlaylistItem(db.Model):
    __tablename__ = 'playlist_item'

    id           = db.Column(db.Integer, primary_key=True)
    playlist_id  = db.Column(db.Integer, db.ForeignKey('playlist.id'), nullable=False, index=True)
    media_id     = db.Column(db.Integer, db.ForeignKey('media.id'),    nullable=True)
    position     = db.Column(db.Integer, nullable=False)
    duration     = db.Column(db.Integer, default=10)
    aspect_mode  = db.Column(db.String(20), nullable=True)
    plugin_type  = db.Column(db.String(50))
    plugin_config = db.Column(db.JSON)
    clip_start   = db.Column(db.Float, nullable=True)
    clip_end     = db.Column(db.Float, nullable=True)

    # Per-item video mute. Player honors this when rendering <video>.
    mute_audio   = db.Column(db.Boolean, default=False, nullable=False)

    # Phase 2: per-item transition (cut | crossfade | fade | wipe).
    transition   = db.Column(db.String(20), default='cut', nullable=False)

    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    def to_dict(self):
        effective_duration = self.duration
        try:
            from media_duration import playlist_item_duration_seconds
            effective_duration = playlist_item_duration_seconds(self)
        except Exception:
            pass
        return {
            'id':           self.id,
            'playlist_id':  self.playlist_id,
            'media_id':     self.media_id,
            'position':     self.position,
            'duration':     self.duration,
            'effective_duration': effective_duration,
            'aspect_mode':  self.aspect_mode,
            'plugin_type':  self.plugin_type,
            'plugin_config': self.plugin_config,
            'clip_start':   self.clip_start,
            'clip_end':     self.clip_end,
            'mute_audio':   self.mute_audio,
            'transition':   self.transition,
            'media':        self.media.to_dict() if self.media else None,
        }


# -----------------------------------------------------------------------------
# Schedule (now with timezone)
# -----------------------------------------------------------------------------
class Schedule(db.Model, TenantModel):
    __tablename__ = 'schedule'

    id           = db.Column(db.Integer, primary_key=True)
    domain_id    = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    name         = db.Column(db.String(120), nullable=False)
    playlist_id  = db.Column(db.Integer, db.ForeignKey('playlist.id'),     nullable=False)
    display_id   = db.Column(db.Integer, db.ForeignKey('display.id'),      nullable=True)
    group_id     = db.Column(db.Integer, db.ForeignKey('display_group.id'),nullable=True)
    start_date   = db.Column(db.Date)
    end_date     = db.Column(db.Date)
    start_time   = db.Column(db.Time)
    end_time     = db.Column(db.Time)
    days_of_week = db.Column(db.String(20))
    priority     = db.Column(db.Integer, default=0)
    is_active    = db.Column(db.Boolean, default=True, nullable=False)

    # IANA timezone (e.g. "America/New_York"). NULL means use the domain
    # default, which falls back to system_settings.default_timezone.
    timezone     = db.Column(db.String(64), nullable=True)

    created_by_user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)

    domain       = db.relationship('Domain')
    group        = db.relationship('DisplayGroup')

    def to_dict(self):
        return {
            'id':           self.id,
            'domain_id':    self.domain_id,
            'name':         self.name,
            'playlist_id':  self.playlist_id,
            'display_id':   self.display_id,
            'group_id':     self.group_id,
            'start_date':   self.start_date.isoformat() if self.start_date else None,
            'end_date':     self.end_date.isoformat()   if self.end_date   else None,
            'start_time':   self.start_time.isoformat() if self.start_time else None,
            'end_time':     self.end_time.isoformat()   if self.end_time   else None,
            'days_of_week': self.days_of_week,
            'priority':     self.priority,
            'is_active':    self.is_active,
            'timezone':     self.timezone,
            'created_at':   self.created_at.isoformat() if self.created_at else None,
        }



# -----------------------------------------------------------------------------
# Plugin policy - Phase 3
#
# Tenant-scoped enable/disable + permission-grant matrix for plugins.
# Plugins declare a set of permissions in their plugin.json; this table
# lets a domain admin override which plugins can be used in their tenant
# and which subset of the declared permissions are actually granted.
#
# Absence of a row means "use the default policy" (all declared
# permissions granted, plugin enabled). This keeps existing installs
# working unchanged after the migration runs.
# -----------------------------------------------------------------------------

class DomainPluginPolicy(db.Model, TenantModel):
    __tablename__ = 'domain_plugin_policy'

    id          = db.Column(db.Integer, primary_key=True)
    domain_id   = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    plugin_key  = db.Column(db.String(80), nullable=False)
    enabled     = db.Column(db.Boolean, default=True, nullable=False)
    # JSON list of permission strings. NULL means "grant whatever the
    # plugin declares" (the implicit default).
    granted_permissions = db.Column(db.JSON, nullable=True)
    updated_at  = db.Column(db.DateTime, default=datetime.utcnow,
                              onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint('domain_id', 'plugin_key',
                          name='uq_plugin_policy_domain_key'),
    )

    domain = db.relationship('Domain')

    def to_dict(self):
        return {
            'id':                  self.id,
            'domain_id':           self.domain_id,
            'plugin_key':          self.plugin_key,
            'enabled':             bool(self.enabled),
            'granted_permissions': self.granted_permissions,
            'updated_at':          self.updated_at.isoformat() if self.updated_at else None,
        }


# -----------------------------------------------------------------------------
# Proof of Play - Phase 4 (optional per spec)
#
# Append-only record of "this content played on this display at this time".
# Written by the player via POST /api/display/<token>/proof-of-play and
# pruned by the same retention sweep that prunes audit rows. Tenant-scoped
# so each domain only sees its own playback evidence.
# -----------------------------------------------------------------------------
class ProofOfPlay(db.Model, TenantModel):
    __tablename__ = 'proof_of_play'

    id          = db.Column(db.Integer, primary_key=True)
    domain_id   = db.Column(db.Integer, db.ForeignKey('domain.id', ondelete='CASCADE'),
                              nullable=False, index=True)
    display_id  = db.Column(db.Integer, db.ForeignKey('display.id', ondelete='CASCADE'),
                              nullable=False, index=True)

    # The thing that played. One of these will be set; both are nullable
    # so we can record plugin / external-URL slides too.
    media_id    = db.Column(db.Integer, db.ForeignKey('media.id', ondelete='SET NULL'),
                              nullable=True, index=True)
    playlist_id = db.Column(db.Integer, db.ForeignKey('playlist.id', ondelete='SET NULL'),
                              nullable=True, index=True)
    item_type   = db.Column(db.String(32), nullable=True)   # image|video|webpage|plugin
    item_name   = db.Column(db.String(255), nullable=True)  # snapshot at play-time
    plugin_key  = db.Column(db.String(80),  nullable=True)

    started_at  = db.Column(db.DateTime, default=datetime.utcnow,
                              nullable=False, index=True)
    duration_ms = db.Column(db.Integer, nullable=True)      # actual time on screen
    completed   = db.Column(db.Boolean, default=True, nullable=False)
    server_received_at = db.Column(db.DateTime, default=datetime.utcnow,
                              nullable=False)

    display     = db.relationship('Display')
    media       = db.relationship('Media')
    playlist    = db.relationship('Playlist')

    __table_args__ = (
        Index('ix_pop_domain_started', 'domain_id', 'started_at'),
        Index('ix_pop_display_started', 'display_id', 'started_at'),
        Index('ix_pop_media_started',   'media_id',   'started_at'),
    )

    def to_dict(self):
        return {
            'id':                 self.id,
            'domain_id':          self.domain_id,
            'display_id':         self.display_id,
            'media_id':           self.media_id,
            'playlist_id':        self.playlist_id,
            'item_type':          self.item_type,
            'item_name':          self.item_name,
            'plugin_key':         self.plugin_key,
            'started_at':         self.started_at.isoformat() if self.started_at else None,
            'duration_ms':        self.duration_ms,
            'completed':          bool(self.completed),
            'server_received_at': self.server_received_at.isoformat() if self.server_received_at else None,
        }
