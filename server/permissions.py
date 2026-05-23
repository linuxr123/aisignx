"""
Permissions + RBAC helpers - Phase 1, Tasks 3 + 8.

Single permission check function `has_permission()` used by both UI route
handlers and API handlers. Decorator `require_permission()` raises 403
on failure.

Permission keys
---------------
Hierarchical dot-form. Each key is a single capability:

    media.read        media.upload     media.edit       media.delete
    playlist.read     playlist.edit    playlist.delete
    display.read      display.assign   display.control  display.lockdown
    schedule.read     schedule.edit    schedule.delete
    group.read        group.edit       group.delete
    plugin.use        plugin.install   plugin.uninstall
    emergency.use     emergency.manage
    audit.read
    settings.read     settings.write
    domain.admin                       -- full control within a domain
    domain.create     domain.delete    -- superadmin only

System roles (seeded by bootstrap)
----------------------------------
    domain_admin     - all permissions within their domain (domain.admin
                       implies the rest)
    content_editor   - media/playlist/schedule edit, display read
    display_operator - display read/control/lockdown, emergency use
    viewer           - read-only across all resources
"""
from functools import wraps

from flask import g, jsonify, request, abort
from flask_login import current_user

from models import db, Permission, Role, UserDomainRole, role_permission


# -----------------------------------------------------------------------------
# Canonical permission keys (single source of truth, used by bootstrap)
# -----------------------------------------------------------------------------
PERMISSIONS = {
    'media.read':         'View media library',
    'media.upload':       'Upload new media',
    'media.edit':         'Edit media metadata and variants',
    'media.delete':       'Delete media',

    'playlist.read':      'View playlists',
    'playlist.edit':      'Create or modify playlists',
    'playlist.delete':    'Delete playlists',

    'display.read':       'View displays',
    'display.assign':     'Assign playlists/schedules to displays',
    'display.control':    'Send playback commands to displays (next/back/reload)',
    'display.lockdown':   'Configure display lockdown settings (PIN, input)',

    'schedule.read':      'View schedules',
    'schedule.edit':      'Create or modify schedules',
    'schedule.delete':    'Delete schedules',

    'group.read':         'View display groups',
    'group.edit':         'Create or modify display groups',
    'group.delete':       'Delete display groups',

    'plugin.use':         'Use plugins in playlists',
    'plugin.install':     'Install / upload plugins',
    'plugin.uninstall':   'Remove or disable plugins',

    'emergency.use':      'Trigger emergency broadcasts',
    'emergency.manage':   'Manage emergency templates',

    'audit.read':         'View audit log',

    'settings.read':      'Read system settings',
    'settings.write':     'Modify system settings',

    'domain.admin':       'Full administrative control within a domain',

    'domain.create':      'Create new domains (superadmin)',
    'domain.delete':      'Delete domains (superadmin)',
}


# Role -> permission keys mapping. Used by bootstrap to seed system roles.
SYSTEM_ROLES = {
    'domain_admin': {
        'description': 'Full administrative control within the domain.',
        'permissions': [
            'media.read', 'media.upload', 'media.edit', 'media.delete',
            'playlist.read', 'playlist.edit', 'playlist.delete',
            'display.read', 'display.assign', 'display.control', 'display.lockdown',
            'schedule.read', 'schedule.edit', 'schedule.delete',
            'group.read', 'group.edit', 'group.delete',
            'plugin.use',
            'emergency.use', 'emergency.manage',
            'audit.read',
            'settings.read', 'settings.write',
            'domain.admin',
        ],
    },
    'content_editor': {
        'description': 'Create and edit content. Cannot manage displays or settings.',
        'permissions': [
            'media.read', 'media.upload', 'media.edit', 'media.delete',
            'playlist.read', 'playlist.edit', 'playlist.delete',
            'schedule.read', 'schedule.edit',
            'group.read',
            'display.read',
            'plugin.use',
        ],
    },
    'display_operator': {
        'description': 'Operate displays. Read content but cannot edit.',
        'permissions': [
            'media.read', 'playlist.read', 'schedule.read', 'group.read',
            'display.read', 'display.control', 'display.lockdown',
            'emergency.use',
            'plugin.use',
        ],
    },
    'viewer': {
        'description': 'Read-only access to all resources within the domain.',
        'permissions': [
            'media.read', 'playlist.read', 'schedule.read',
            'group.read', 'display.read',
            'audit.read',
            'settings.read',
        ],
    },
    'media_manager': {
        'description': 'Manage media library only (upload, edit, delete).',
        'permissions': [
            'media.read', 'media.upload', 'media.edit', 'media.delete',
        ],
    },
}


# Maps REST API token scopes to RBAC permission keys checked by routes.
SCOPE_TO_PERMISSIONS = {
    'media:read':     frozenset({'media.read'}),
    'media:write':    frozenset({'media.read', 'media.upload', 'media.edit', 'media.delete'}),
    'playlist:read':  frozenset({'playlist.read'}),
    'playlist:write': frozenset({'playlist.read', 'playlist.edit', 'playlist.delete'}),
    'display:read':   frozenset({'display.read'}),
    'display:write':  frozenset({'display.read', 'display.assign', 'display.control', 'display.lockdown'}),
    'group:read':     frozenset({'group.read'}),
    'group:write':    frozenset({'group.read', 'group.edit', 'group.delete'}),
    'schedule:read':  frozenset({'schedule.read'}),
    'schedule:write': frozenset({'schedule.read', 'schedule.edit', 'schedule.delete'}),
    'emergency:read': frozenset({'emergency.use', 'emergency.manage'}),
    'emergency:write': frozenset({'emergency.use', 'emergency.manage'}),
}


def token_grants_permission(token, key):
    """True when the bearer token's scopes include permission `key`."""
    if token is None or not key:
        return False
    scopes = [s.strip() for s in (token.scopes or '').split(',') if s.strip()]
    for scope in scopes:
        if key in SCOPE_TO_PERMISSIONS.get(scope, ()):
            return True
    return False


def scopes_grantable_by_user(user, domain_id):
    """API scope strings a user may assign when creating tokens in a tenant."""
    if user is None:
        return set()
    if getattr(user, 'is_superadmin', False):
        return set(SCOPE_TO_PERMISSIONS.keys())
    keys = _user_permission_keys(user, domain_id)
    grantable = set()
    for scope, perms in SCOPE_TO_PERMISSIONS.items():
        if perms.issubset(keys):
            grantable.add(scope)
    return grantable


# -----------------------------------------------------------------------------
# Permission queries
# -----------------------------------------------------------------------------
def _user_permission_keys(user, domain_id):
    """All permission keys the user holds in the given domain. Cached per
    request via flask.g."""
    cache_key = f'_perm_cache:{user.id}:{domain_id}'
    if hasattr(g, cache_key):
        return getattr(g, cache_key)

    if getattr(user, 'is_superadmin', False):
        keys = set(PERMISSIONS.keys())
    else:
        rows = (db.session.query(Permission.key)
                .join(role_permission, role_permission.c.permission_id == Permission.id)
                .join(Role, Role.id == role_permission.c.role_id)
                .join(UserDomainRole, UserDomainRole.role_id == Role.id)
                .filter(UserDomainRole.user_id == user.id)
                .filter(UserDomainRole.domain_id == domain_id)
                .all())
        keys = {r[0] for r in rows}
        # domain.admin is a meta-permission: implies all non-superadmin perms.
        if 'domain.admin' in keys:
            keys.update(k for k in PERMISSIONS if not k.startswith('domain.create')
                        and not k.startswith('domain.delete'))

    setattr(g, cache_key, keys)
    return keys


def has_permission(user, key, domain_id=None):
    """True iff `user` has permission `key` in the given domain.
    domain_id None -> uses tenant_filter.current_domain_id().

    Bearer-token requests: service accounts are limited to token scopes;
    interactive users must have both RBAC and matching token scopes."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return False
    if domain_id is None:
        from tenant_filter import current_domain_id
        domain_id = current_domain_id()

    from flask import g
    token = getattr(g, 'api_token', None)
    if token is not None:
        if getattr(user, 'is_service_account', False):
            return token_grants_permission(token, key)
        if not token_grants_permission(token, key):
            return False

    if getattr(user, 'is_superadmin', False):
        return True
    if domain_id is None:
        return False
    return key in _user_permission_keys(user, domain_id)


def _effective_principal():
    """Return the user object representing the requester. Prefers an API
    token's bound user (set by utils.api_auth_required) over the Flask-Login
    session user, so token-authenticated requests get permission-checked
    against the token's owner."""
    api_user = getattr(g, 'api_user', None)
    if api_user is not None and getattr(api_user, 'is_authenticated', False):
        return api_user
    return current_user


def require_permission(key):
    """Decorator: 403 if the current user doesn't have `key` in the active
    tenant. Used on both UI and API handlers (returns JSON for /api/* paths,
    HTML 403 for everything else).

    Works with both session auth (current_user) and API-token auth
    (g.api_user, set by utils.api_auth_required). Stack with @login_required
    or @api_auth_required first to ensure a principal is established."""
    def decorator(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            principal = _effective_principal()
            if not has_permission(principal, key):
                if request.path.startswith('/api/'):
                    return jsonify({'status': 'error',
                                    'message': 'forbidden',
                                    'permission': key}), 403
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return decorator


def all_permissions():
    """Return list of (key, description) tuples for UI rendering."""
    return sorted(PERMISSIONS.items())


# Grouped permission catalog for the role builder UI (resource → capabilities).
PERMISSION_GROUPS = [
    {
        'id': 'media',
        'label': 'Media library',
        'description': 'Upload, organize, and delete signage media',
        'permissions': [
            {'key': 'media.read',   'label': 'View'},
            {'key': 'media.upload', 'label': 'Upload'},
            {'key': 'media.edit',   'label': 'Edit'},
            {'key': 'media.delete', 'label': 'Delete'},
        ],
    },
    {
        'id': 'playlist',
        'label': 'Playlists',
        'description': 'Build and manage what displays play',
        'permissions': [
            {'key': 'playlist.read',   'label': 'View'},
            {'key': 'playlist.edit',   'label': 'Create & edit'},
            {'key': 'playlist.delete', 'label': 'Delete'},
        ],
    },
    {
        'id': 'display',
        'label': 'Displays',
        'description': 'Screens, playback control, and lockdown',
        'permissions': [
            {'key': 'display.read',      'label': 'View'},
            {'key': 'display.assign',    'label': 'Assign content'},
            {'key': 'display.control',   'label': 'Remote control'},
            {'key': 'display.lockdown',  'label': 'Lockdown settings'},
        ],
    },
    {
        'id': 'schedule',
        'label': 'Schedules',
        'description': 'When playlists run on displays and groups',
        'permissions': [
            {'key': 'schedule.read',   'label': 'View'},
            {'key': 'schedule.edit',   'label': 'Create & edit'},
            {'key': 'schedule.delete', 'label': 'Delete'},
        ],
    },
    {
        'id': 'group',
        'label': 'Display groups',
        'description': 'Organize displays into groups and subgroups',
        'permissions': [
            {'key': 'group.read',   'label': 'View'},
            {'key': 'group.edit',   'label': 'Create & edit'},
            {'key': 'group.delete', 'label': 'Delete'},
        ],
    },
    {
        'id': 'plugin',
        'label': 'Plugins',
        'description': 'Plugin content and installation',
        'permissions': [
            {'key': 'plugin.use',         'label': 'Use in playlists'},
            {'key': 'plugin.install',     'label': 'Install / upload',
             'superadmin_only': True},
            {'key': 'plugin.uninstall',   'label': 'Uninstall',
             'superadmin_only': True},
        ],
    },
    {
        'id': 'emergency',
        'label': 'Emergency broadcasts',
        'description': 'Override normal playback for urgent messages',
        'permissions': [
            {'key': 'emergency.use',    'label': 'Trigger'},
            {'key': 'emergency.manage', 'label': 'Manage templates'},
        ],
    },
    {
        'id': 'audit',
        'label': 'Audit & compliance',
        'description': 'Review who changed what',
        'permissions': [
            {'key': 'audit.read', 'label': 'View audit log'},
        ],
    },
    {
        'id': 'settings',
        'label': 'System settings',
        'description': 'Tenant configuration and integrations',
        'permissions': [
            {'key': 'settings.read',  'label': 'View'},
            {'key': 'settings.write', 'label': 'Modify'},
        ],
    },
    {
        'id': 'tenant',
        'label': 'Tenant administration',
        'description': 'Full control within a tenant (superadmin-only items noted)',
        'permissions': [
            {'key': 'domain.admin',   'label': 'Tenant admin (all capabilities)'},
            {'key': 'domain.create',  'label': 'Create tenants', 'superadmin_only': True},
            {'key': 'domain.delete',  'label': 'Delete tenants', 'superadmin_only': True},
        ],
    },
]


def permission_groups(for_superadmin=True):
    """Return permission groups for the role editor, optionally hiding
    superadmin-only keys for tenant-scoped custom roles."""
    groups = []
    for gdef in PERMISSION_GROUPS:
        perms = []
        for p in gdef['permissions']:
            if p.get('superadmin_only') and not for_superadmin:
                continue
            entry = dict(p)
            entry['description'] = PERMISSIONS.get(p['key'], '')
            perms.append(entry)
        if perms:
            groups.append({
                'id': gdef['id'],
                'label': gdef['label'],
                'description': gdef['description'],
                'permissions': perms,
            })
    return groups
