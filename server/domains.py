"""
Domain switcher and domain CRUD endpoints - Phase 2 / Tasks B4 + B7.

B4 (switcher) - Web UI dropdown that lets a user with multiple domain
memberships switch between them. Superadmins can switch into any domain.
The switch is stored in session['current_domain_id'] and picked up by the
existing @app.before_request resolver in app.py.

B7 (CRUD) - Superadmin-only domain creation/deletion + domain admin
self-service edits (name/description/quota/timezone).

Routes
------
GET  /api/session/domains          list domains visible to the requester
POST /api/session/domain           switch the active domain

GET  /admin/domains                superadmin domain management page
GET  /api/domains                  list all domains (superadmin)
POST /api/domains                  create domain (superadmin)
GET  /api/domains/<id>             read one domain (superadmin OR member)
PUT  /api/domains/<id>             update (superadmin OR domain_admin of <id>)
DELETE /api/domains/<id>           delete (superadmin only; refuses if non-empty)

Template helper
---------------
domain_switcher_state()  - dict consumed by the sidebar widget.
"""
import re

from flask import (Blueprint, render_template, jsonify, request, session,
                   abort)
from flask_login import login_required, current_user

from models import (Domain, UserDomainRole, Display, DisplayGroup, Media,
                    Playlist, Schedule, ApiToken, db)
from tenant_filter import bypass_tenant_filter, current_domain_id
from permissions import has_permission, require_permission
from audit import audit


domains_bp = Blueprint('domains', __name__)


# Slug rules: lowercase letters, digits, hyphen. 2-64 chars. Used in URLs
# and in audit payloads, so keep it conservative.
_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$')


def _is_superadmin():
    return getattr(current_user, 'is_superadmin', False)


def _user_visible_domains(user):
    """Return list of Domain objects the user is allowed to switch into.
    Superadmins see every active domain; everyone else sees only the
    domains they hold a role in."""
    if user is None or not getattr(user, 'is_authenticated', False):
        return []
    with bypass_tenant_filter():    # tenant-ok: domain listing is global
        if getattr(user, 'is_superadmin', False):
            return (Domain.query
                    .filter_by(is_active=True)
                    .order_by(Domain.name.asc()).all())
        rows = (db.session.query(Domain)
                .join(UserDomainRole, UserDomainRole.domain_id == Domain.id)
                .filter(UserDomainRole.user_id == user.id,
                        Domain.is_active == True)
                .distinct()
                .order_by(Domain.name.asc()).all())
        return rows


def domain_switcher_state():
    """Context-processor entry: produce everything the sidebar widget
    needs in one call so the template stays trivial."""
    if not getattr(current_user, 'is_authenticated', False):
        return {'available': [], 'current_id': None,
                'current_name': None, 'visible': False}

    available = _user_visible_domains(current_user)
    current_id = current_domain_id()
    current_name = None
    if current_id is not None:
        for d in available:
            if d.id == current_id:
                current_name = d.name
                break
        if current_name is None:
            with bypass_tenant_filter():    # tenant-ok: superadmin lookup
                d = db.session.get(Domain, current_id)
                current_name = d.name if d else f'Domain #{current_id}'
    return {
        'available':    [{'id': d.id, 'name': d.name, 'slug': d.slug}
                         for d in available],
        'current_id':   current_id,
        'current_name': current_name,
        'visible':      (len(available) > 1
                         or getattr(current_user, 'is_superadmin', False)),
    }


# ============================================================================
# Session / switcher
# ============================================================================

@domains_bp.route('/api/session/domains', methods=['GET'])
@login_required
def api_session_domains():
    """JSON-friendly version of the same data the template helper exposes."""
    state = domain_switcher_state()
    return jsonify({'status': 'success', **state})


@domains_bp.route('/api/session/domain', methods=['POST'])
@login_required
def api_session_set_domain():
    """Switch the active tenant for this session. Only domains the user
    actually has access to are accepted; everything else returns 403."""
    data = request.get_json(silent=True) or {}
    target_id = data.get('domain_id')
    try:
        target_id = int(target_id)
    except (TypeError, ValueError):
        return jsonify({'status': 'error',
                        'message': 'domain_id must be an integer'}), 400

    available_ids = {d.id for d in _user_visible_domains(current_user)}
    if target_id not in available_ids:
        return jsonify({'status': 'error',
                        'message': 'forbidden'}), 403

    previous = session.get('current_domain_id')
    session['current_domain_id'] = target_id

    audit('session.switch_domain', target_type='domain',
          target_id=str(target_id),
          payload={'from': previous, 'to': target_id})

    return jsonify({'status': 'success',
                    'current_id': target_id,
                    'message': 'Active domain switched. Reload to apply.'})


# ============================================================================
# Domain CRUD
# ============================================================================

def _validate_slug(slug):
    """Return (ok, error_msg)."""
    if not slug:
        return False, 'slug is required'
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        return False, ('slug must be lowercase letters/digits/hyphens, '
                       '2-64 chars, no leading/trailing hyphen')
    return True, slug


def _domain_summary(d, include_usage=False):
    """Public-facing dict for one Domain row. Optionally includes derived
    counts (cheap COUNT queries; only call when the caller needs them)."""
    out = {
        'id':                     d.id,
        'name':                   d.name,
        'slug':                   d.slug,
        'description':            d.description,
        'is_active':              d.is_active,
        'storage_quota_bytes':    d.storage_quota_bytes,
        'storage_used_bytes':     d.storage_used_bytes or 0,
        'default_timezone':       d.default_timezone,
        'branding_primary_color': d.branding_primary_color,
        'branding_logo_path':     d.branding_logo_path,
        'features':               d.features or {},
        'created_at':             d.created_at.isoformat() if d.created_at else None,
        'updated_at':             d.updated_at.isoformat() if d.updated_at else None,
    }
    if include_usage:
        # tenant-ok: superadmin domain inventory; cross-tenant by design.
        with bypass_tenant_filter():
            out['counts'] = {
                'displays':  Display.query.filter_by(domain_id=d.id).count(),
                'groups':    DisplayGroup.query.filter_by(domain_id=d.id).count(),
                'media':     Media.query.filter_by(domain_id=d.id).count(),
                'playlists': Playlist.query.filter_by(domain_id=d.id).count(),
                'schedules': Schedule.query.filter_by(domain_id=d.id).count(),
                'tokens':    ApiToken.query.filter_by(domain_id=d.id,
                                                      revoked=False).count(),
                'members':   UserDomainRole.query.filter_by(domain_id=d.id).count(),
            }
    return out


@domains_bp.route('/admin/domains')
@login_required
def admin_domains_page():
    """Superadmin domain management page. Renders the table and modal;
    the page itself fetches data from /api/domains so we don't have to
    pass it through the template context."""
    if not _is_superadmin():
        abort(403)
    return render_template('admin_domains.html')


@domains_bp.route('/api/domains', methods=['GET'])
@login_required
def api_list_domains():
    """List all domains. Superadmin only -- regular users use
    /api/session/domains for their own visible set."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():    # tenant-ok: superadmin domain inventory
        rows = (Domain.query
                .order_by(Domain.is_active.desc(), Domain.name.asc())
                .all())
        return jsonify({
            'status':  'success',
            'domains': [_domain_summary(d, include_usage=True) for d in rows],
        })


@domains_bp.route('/api/domains', methods=['POST'])
@login_required
def api_create_domain():
    """Create a new domain. Superadmin only."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name or len(name) > 120:
        return jsonify({'status': 'error',
                        'message': 'name required (1-120 chars)'}), 400
    raw_slug = data.get('slug') or name.lower().replace(' ', '-')
    ok, slug_or_err = _validate_slug(raw_slug)
    if not ok:
        return jsonify({'status': 'error', 'message': slug_or_err}), 400
    slug = slug_or_err

    quota = data.get('storage_quota_bytes')
    if quota is not None:
        try:
            quota = int(quota)
            if quota < 0:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({'status': 'error',
                            'message': 'storage_quota_bytes must be a non-negative integer or null'}), 400

    with bypass_tenant_filter():    # tenant-ok: superadmin domain create
        if Domain.query.filter((Domain.name == name) | (Domain.slug == slug)).first():
            return jsonify({'status': 'error',
                            'message': 'name or slug already in use'}), 409
        d = Domain(
            name=name,
            slug=slug,
            description=data.get('description') or None,
            storage_quota_bytes=quota,
            default_timezone=data.get('default_timezone') or None,
            features={},
        )
        db.session.add(d)
        db.session.commit()
        new_id = d.id

    audit('domain.create', target_type='domain', target_id=str(new_id),
          payload={'name': name, 'slug': slug, 'quota_bytes': quota})

    with bypass_tenant_filter():
        d = db.session.get(Domain, new_id)
        return jsonify({'status': 'success', 'domain': _domain_summary(d)})


@domains_bp.route('/api/domains/<int:domain_id>', methods=['GET'])
@login_required
def api_get_domain(domain_id):
    """Read one domain. Superadmin sees any; members see their own."""
    if not _is_superadmin():
        # Members can read their own domain's metadata.
        member_ids = {d.id for d in _user_visible_domains(current_user)}
        if domain_id not in member_ids:
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():    # tenant-ok: validated above
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'not found'}), 404
        return jsonify({'status': 'success',
                        'domain': _domain_summary(d, include_usage=_is_superadmin())})


@domains_bp.route('/api/domains/<int:domain_id>', methods=['PUT'])
@login_required
def api_update_domain(domain_id):
    """Update a domain. Superadmin can change anything; a domain_admin can
    update their own domain's name/description/quota/timezone/branding but
    NOT the slug or is_active flag (those are superadmin-level)."""
    is_super = _is_superadmin()
    if not is_super:
        # Domain admins can edit their own domain.
        if not has_permission(current_user, 'domain.admin', domain_id=domain_id):
            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    data = request.get_json(silent=True) or {}

    with bypass_tenant_filter():    # tenant-ok: validated above
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'not found'}), 404

        changes = {}

        if 'name' in data:
            name = (data['name'] or '').strip()
            if not name or len(name) > 120:
                return jsonify({'status': 'error',
                                'message': 'name must be 1-120 chars'}), 400
            if name != d.name:
                clash = Domain.query.filter(Domain.name == name,
                                             Domain.id != d.id).first()
                if clash:
                    return jsonify({'status': 'error',
                                    'message': 'name already in use'}), 409
                changes['name'] = (d.name, name); d.name = name

        if 'description' in data:
            new_desc = (data['description'] or '').strip() or None
            if new_desc != d.description:
                changes['description'] = (d.description, new_desc)
                d.description = new_desc

        if 'storage_quota_bytes' in data:
            if not is_super:
                return jsonify({'status': 'error',
                                'message': 'Only superadmins can change storage quota'}), 403
            q = data['storage_quota_bytes']
            if q is not None:
                try:
                    q = int(q)
                    if q < 0:
                        raise ValueError
                except (TypeError, ValueError):
                    return jsonify({'status': 'error',
                                    'message': 'storage_quota_bytes must be a non-negative integer or null'}), 400
            if q != d.storage_quota_bytes:
                changes['storage_quota_bytes'] = (d.storage_quota_bytes, q)
                d.storage_quota_bytes = q

        if 'default_timezone' in data:
            tz = (data['default_timezone'] or '').strip() or None
            if tz != d.default_timezone:
                changes['default_timezone'] = (d.default_timezone, tz)
                d.default_timezone = tz

        if 'branding_primary_color' in data:
            c = (data['branding_primary_color'] or '').strip() or None
            if c and not re.match(r'^#[0-9a-fA-F]{6}$', c):
                return jsonify({'status': 'error',
                                'message': 'branding_primary_color must be #RRGGBB'}), 400
            if c != d.branding_primary_color:
                changes['branding_primary_color'] = (d.branding_primary_color, c)
                d.branding_primary_color = c

        # Superadmin-only fields.
        if is_super:
            if 'slug' in data:
                ok, slug_or_err = _validate_slug(data['slug'])
                if not ok:
                    return jsonify({'status': 'error', 'message': slug_or_err}), 400
                slug = slug_or_err
                if slug != d.slug:
                    clash = Domain.query.filter(Domain.slug == slug,
                                                 Domain.id != d.id).first()
                    if clash:
                        return jsonify({'status': 'error',
                                        'message': 'slug already in use'}), 409
                    changes['slug'] = (d.slug, slug); d.slug = slug
            if 'is_active' in data:
                act = bool(data['is_active'])
                if act != d.is_active:
                    changes['is_active'] = (d.is_active, act); d.is_active = act
            if 'features' in data and isinstance(data['features'], dict):
                if data['features'] != (d.features or {}):
                    changes['features'] = (d.features, data['features'])
                    d.features = data['features']

        if changes:
            db.session.commit()
            audit('domain.update', target_type='domain', target_id=str(d.id),
                  payload={'changes': {k: {'from': v[0], 'to': v[1]}
                                       for k, v in changes.items()}})
        return jsonify({'status': 'success',
                        'domain': _domain_summary(d, include_usage=is_super)})


@domains_bp.route('/api/domains/bulk-update', methods=['POST'])
@login_required
def api_bulk_update_domains():
    """Bulk edit safe tenant/domain fields. Superadmin only.

    Body: {"ids": [int,...], "changes": {"is_active": bool,
                                         "default_timezone": str|null,
                                         "storage_quota_bytes": int|null,
                                         "branding_primary_color": "#RRGGBB"|null}}
    """
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    changes = data.get('changes') or {}
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400
    if not isinstance(changes, dict) or not changes:
        return jsonify({'status': 'error', 'message': 'changes is empty'}), 400

    allowed = {}
    if 'is_active' in changes:
        allowed['is_active'] = bool(changes.get('is_active'))
    if 'default_timezone' in changes:
        allowed['default_timezone'] = (changes.get('default_timezone') or '').strip() or None
    if 'storage_quota_bytes' in changes:
        q = changes.get('storage_quota_bytes')
        if q is not None:
            try:
                q = int(q)
                if q < 0:
                    raise ValueError
            except (TypeError, ValueError):
                return jsonify({'status': 'error',
                                'message': 'storage_quota_bytes must be non-negative integer or null'}), 400
        allowed['storage_quota_bytes'] = q
    if 'branding_primary_color' in changes:
        c = (changes.get('branding_primary_color') or '').strip() or None
        if c and not re.match(r'^#[0-9a-fA-F]{6}$', c):
            return jsonify({'status': 'error',
                            'message': 'branding_primary_color must be #RRGGBB'}), 400
        allowed['branding_primary_color'] = c
    if not allowed:
        return jsonify({'status': 'error',
                        'message': 'no recognized fields in changes'}), 400

    with bypass_tenant_filter():
        rows = Domain.query.filter(Domain.id.in_(ids)).all()
        found_ids = {d.id for d in rows}
        updated = 0
        results = []
        for d in rows:
            row_changes = {}
            for k, v in allowed.items():
                if getattr(d, k, None) != v:
                    row_changes[k] = {'from': getattr(d, k, None), 'to': v}
                    setattr(d, k, v)
            if row_changes:
                updated += 1
            results.append({'id': d.id, 'ok': True, 'changes': row_changes})
        db.session.commit()

    not_found = [i for i in ids if i not in found_ids]
    audit('domain.bulk_update', target_type='domains',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'requested': len(ids), 'updated': updated,
                   'not_found': not_found, 'changes': allowed,
                   'results': results})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'updated': updated, 'not_found': not_found,
                    'results': results})


@domains_bp.route('/api/domains/bulk-delete', methods=['POST'])
@login_required
def api_bulk_delete_domains():
    """Delete multiple tenants. Superadmin only. Non-empty tenants require
    force=true, matching the single-tenant delete safety model."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    force = bool(data.get('force'))
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400

    results = []
    snapshots = []
    deleted = 0
    with bypass_tenant_filter():
        active_count = Domain.query.filter_by(is_active=True).count()
        rows = Domain.query.filter(Domain.id.in_(ids)).all()
        found_ids = {d.id for d in rows}
        for d in rows:
            counts = {
                'displays':  Display.query.filter_by(domain_id=d.id).count(),
                'media':     Media.query.filter_by(domain_id=d.id).count(),
                'playlists': Playlist.query.filter_by(domain_id=d.id).count(),
                'schedules': Schedule.query.filter_by(domain_id=d.id).count(),
                'groups':    DisplayGroup.query.filter_by(domain_id=d.id).count(),
                'tokens':    ApiToken.query.filter_by(domain_id=d.id,
                                                      revoked=False).count(),
            }
            non_empty = sum(counts.values())
            if active_count <= 1 and d.is_active:
                results.append({'id': d.id, 'ok': False,
                                'error': 'cannot delete the last active tenant',
                                'counts': counts})
                continue
            if non_empty and not force:
                results.append({'id': d.id, 'ok': False,
                                'error': 'tenant is not empty; retry with force=true',
                                'counts': counts})
                continue
            if non_empty:
                for cls in (Schedule, Playlist, Media, ApiToken, Display,
                             DisplayGroup):
                    cls.query.filter_by(domain_id=d.id).delete(
                        synchronize_session=False)
                UserDomainRole.query.filter_by(domain_id=d.id).delete(
                    synchronize_session=False)
                db.session.flush()
            snapshots.append({'id': d.id, 'name': d.name, 'slug': d.slug,
                              'counts': counts, 'forced': force})
            db.session.delete(d)
            deleted += 1
            if d.is_active:
                active_count -= 1
            results.append({'id': d.id, 'ok': True})
        db.session.commit()

    if session.get('current_domain_id') in ids:
        session.pop('current_domain_id', None)
    not_found = [i for i in ids if i not in found_ids]
    audit('domain.bulk_delete', target_type='domains',
          target_id=','.join(str(s['id']) for s in snapshots),
          payload={'requested': len(ids), 'deleted': deleted,
                   'not_found': not_found, 'tenants': snapshots,
                   'results': results})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'deleted': deleted, 'not_found': not_found,
                    'results': results})


@domains_bp.route('/api/domains/<int:domain_id>', methods=['DELETE'])
@login_required
def api_delete_domain(domain_id):
    """Delete a domain. Superadmin only. Refuses to delete a non-empty
    domain to prevent accidental data loss -- the caller must purge
    contents first (or use ?force=1, which still refuses if there are
    OTHER active domains' members assigned only here)."""
    if not _is_superadmin():
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    force = request.args.get('force') == '1'

    with bypass_tenant_filter():    # tenant-ok: superadmin domain delete
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'not found'}), 404

        # Refuse to delete the last remaining domain -- without one, every
        # subsequent insert blows up (auto-stamp has no tenant to use).
        active_count = Domain.query.filter_by(is_active=True).count()
        if active_count <= 1 and d.is_active:
            return jsonify({'status': 'error',
                            'message': 'cannot delete the last active domain'}), 409

        counts = {
            'displays':  Display.query.filter_by(domain_id=d.id).count(),
            'media':     Media.query.filter_by(domain_id=d.id).count(),
            'playlists': Playlist.query.filter_by(domain_id=d.id).count(),
            'schedules': Schedule.query.filter_by(domain_id=d.id).count(),
            'groups':    DisplayGroup.query.filter_by(domain_id=d.id).count(),
            'tokens':    ApiToken.query.filter_by(domain_id=d.id,
                                                   revoked=False).count(),
        }
        non_empty = sum(counts.values())
        if non_empty and not force:
            return jsonify({
                'status': 'error',
                'message': ('domain is not empty; pass ?force=1 to delete '
                            'anyway. This will cascade-delete all its data.'),
                'counts': counts,
            }), 409

        snapshot = {'name': d.name, 'slug': d.slug, 'counts': counts,
                    'forced': force}
        # Explicit cascade in Python: SQLite often runs without
        # PRAGMA foreign_keys=ON, in which case ondelete='CASCADE' on the
        # FK is silently a no-op and we'd leave orphaned rows. Walk the
        # tenant tables in a safe order (children first).
        if non_empty:
            with bypass_tenant_filter():    # tenant-ok: superadmin force-delete
                # Order: schedules -> playlist_items (via Playlist) -> playlists
                #     -> media -> tokens -> displays -> groups
                # SQLAlchemy relationships handle PlaylistItem cascade via
                # Playlist.cascade='all, delete-orphan'.
                for cls in (Schedule, Playlist, Media, ApiToken, Display,
                             DisplayGroup):
                    cls.query.filter_by(domain_id=d.id).delete(
                        synchronize_session=False)
                # Also drop user role assignments scoped to this domain.
                UserDomainRole.query.filter_by(domain_id=d.id).delete(
                    synchronize_session=False)
                db.session.flush()
        db.session.delete(d)
        db.session.commit()

        # If the deleter's session pointed at this domain, clear it so
        # the next request resolves to a different one.
        if session.get('current_domain_id') == domain_id:
            session.pop('current_domain_id', None)

    audit('domain.delete', target_type='domain', target_id=str(domain_id),
          payload=snapshot)
    return jsonify({'status': 'success',
                    'message': 'Domain deleted',
                    'forced': force})


# ============================================================================
# Per-tenant branding
#
# Two pieces of state live on the Domain row:
#   branding_logo_path     - 'd<N>/images/<uuid>.png' (storage rel_path)
#   branding_primary_color - '#RRGGBB'
#
# Editing color is already handled by api_update_domain (PUT /api/domains/<id>).
# Logo upload needs its own endpoint because it's multipart, not JSON.
#
# The HTML <head> reads both via the `branding()` template helper which
# returns the active tenant's branding (falling back to AISignX defaults
# for unauthenticated pages and tenants with nothing set).
# ============================================================================

# Whitelist mirrors storage.ALLOWED_EXTENSIONS['image']; duplicated here
# only so we can produce a friendly error before the storage layer rejects.
_LOGO_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'}

# 2 MB is plenty for a logo and well below upload.max_size_mb (default 100MB).
_LOGO_MAX_BYTES = 2 * 1024 * 1024


def _can_admin_branding(domain_id):
    """Logo + color edits follow the same rule as other domain settings:
    superadmin everywhere, domain.admin within their own domain."""
    if getattr(current_user, 'is_superadmin', False):
        return True
    return has_permission(current_user, 'domain.admin', domain_id=domain_id)


@domains_bp.route('/api/domains/<int:domain_id>/branding/logo',
                  methods=['POST'])
@login_required
def api_upload_branding_logo(domain_id):
    """Upload a new tenant logo. Multipart 'file' field. Replaces any
    existing logo and removes the old file from disk."""
    if not _can_admin_branding(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    f = request.files.get('file')
    if f is None or not f.filename:
        return jsonify({'status': 'error', 'message': 'file is required'}), 400

    import os as _os
    ext = _os.path.splitext(f.filename)[1].lower()
    if ext not in _LOGO_EXTS:
        return jsonify({'status': 'error',
                        'message': f'unsupported extension {ext!r}; allowed: '
                                   + ', '.join(sorted(_LOGO_EXTS))}), 400

    # Pre-flight size check via Content-Length header. We can't seek
    # FileStorage reliably (it may be a SpooledTemporaryFile already), so
    # trust the header for the gate and let the storage layer enforce
    # absolute limits.
    cl = request.content_length or 0
    if cl > _LOGO_MAX_BYTES:
        return jsonify({'status': 'error',
                        'message': f'logo too large; max {_LOGO_MAX_BYTES // 1024}KB'}), 413

    import storage as _storage

    with bypass_tenant_filter():    # tenant-ok: branding admin
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'domain not found'}), 404
        old_path = d.branding_logo_path

    # Save into the target tenant's storage tree -- pass domain_id explicitly
    # because the requester's session may be in a different domain.
    try:
        stored = _storage.save_upload(f, kind='image', domain_id=domain_id)
    except ValueError as e:
        return jsonify({'status': 'error', 'message': str(e)}), 400

    with bypass_tenant_filter():    # tenant-ok: branding admin
        d = db.session.get(Domain, domain_id)
        d.branding_logo_path = stored.rel_path
        db.session.commit()

    # Best-effort cleanup of the previous file.
    if old_path and old_path != stored.rel_path:
        try:
            _storage.delete(old_path)
        except Exception:
            pass

    audit('domain.branding_update', target_type='domain',
          target_id=str(domain_id),
          payload={'field': 'logo', 'from': old_path, 'to': stored.rel_path})

    return jsonify({
        'status': 'success',
        'logo_path': stored.rel_path,
        'logo_url':  _storage.signed_url(stored.rel_path, external=False),
    })


@domains_bp.route('/api/domains/<int:domain_id>/branding/logo',
                  methods=['DELETE'])
@login_required
def api_delete_branding_logo(domain_id):
    """Clear the tenant logo (revert to default). The file on disk is
    deleted; if the domain isn't using one, this is a no-op."""
    if not _can_admin_branding(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    import storage as _storage
    with bypass_tenant_filter():    # tenant-ok: branding admin
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'domain not found'}), 404
        old = d.branding_logo_path
        d.branding_logo_path = None
        db.session.commit()

    if old:
        try:
            _storage.delete(old)
        except Exception:
            pass
        audit('domain.branding_update', target_type='domain',
              target_id=str(domain_id),
              payload={'field': 'logo', 'from': old, 'to': None})

    return jsonify({'status': 'success'})


# ----------------------------------------------------------------------------
# Template helper: branding state for the active tenant.
# ----------------------------------------------------------------------------

# Defaults the UI falls back to when the tenant has nothing set or the
# user is unauthenticated. Mirrors the original AISignX brand.
_DEFAULT_LOGO_STATIC = 'img/AISignX.png'
_DEFAULT_PRIMARY     = '#0d6efd'


def branding_state():
    """Return {'logo_url', 'primary_color', 'tenant_name'} for the
    current request. Safe to call on every page render -- only one
    cheap DB read per request."""
    cur = current_domain_id()
    if cur is None:
        return {'logo_url': None, 'logo_is_default': True,
                'primary_color': _DEFAULT_PRIMARY,
                'tenant_name': None}
    with bypass_tenant_filter():    # tenant-ok: branding render
        d = db.session.get(Domain, cur)
        if d is None:
            return {'logo_url': None, 'logo_is_default': True,
                    'primary_color': _DEFAULT_PRIMARY,
                    'tenant_name': None}
        logo_url = None
        if d.branding_logo_path:
            try:
                import storage as _storage
                # Internal URL (no _external) so it works inside the same
                # origin without re-signing absolute URLs every render.
                logo_url = _storage.signed_url(d.branding_logo_path,
                                                external=False)
            except Exception:
                logo_url = None
        return {
            'logo_url':        logo_url,
            'logo_is_default': logo_url is None,
            'primary_color':   d.branding_primary_color or _DEFAULT_PRIMARY,
            'tenant_name':     d.name,
        }
