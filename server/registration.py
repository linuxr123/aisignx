import json
import os
import secrets
import uuid
from datetime import datetime

from flask import Blueprint, request, jsonify, render_template, abort
from flask_login import login_required, current_user
from models import db, Display, PendingDisplay, Domain
from rate_limit import limit_per_ip
from tenant_filter import bypass_tenant_filter, current_domain_id
from permissions import has_permission


# Generic, non-leaky error returned when a device fails to authenticate.
# The same string is used whether the code is missing, malformed, expired,
# revoked, belongs to a disabled tenant, or simply doesn't exist — so a
# device cannot probe /api/register to enumerate tenants or test codes.
_BAD_CODE_MSG = 'Invalid or missing enrollment code'

# Hard cap on pending rows per domain. Stops a hostile device from filling
# a tenant's approval queue with thousands of bogus pendings even when it
# has a valid code. Admins can clear pendings to make room.
_MAX_PENDING_PER_DOMAIN = 100


def _normalize_code(value):
    """Strip whitespace, dashes and case from a user-typed code so
    'A1B2-C3D4' and 'a1b2c3d4' resolve identically."""
    if not isinstance(value, str):
        return ''
    return value.strip().replace('-', '').replace(' ', '').upper()


def _resolve_domain_from_code(raw):
    """Return a Domain whose enrollment is currently open and whose code
    matches `raw`, or None. Always uses bypass_tenant_filter because the
    caller is unauthenticated and the lookup spans every tenant."""
    code = _normalize_code(raw)
    if not code:
        return None
    with bypass_tenant_filter():
        d = Domain.query.filter_by(enrollment_code=code,
                                    is_active=True).first()
        if d is None:
            return None
        if not d.enrollment_is_open():
            return None
        return d


def _is_superadmin():
    return bool(getattr(current_user, 'is_superadmin', False))


def _can_admin_enrollment(domain_id):
    """Who's allowed to mint/rotate/revoke a tenant's enrollment code:
    superadmin, or the tenant's own domain.admin."""
    if _is_superadmin():
        return True
    return has_permission(current_user, 'domain.admin', domain_id=domain_id)


def _generate_code():
    """16 hex chars (~64 bits of entropy). Short enough to type, long
    enough that brute-forcing /api/register is hopeless even without
    rate-limiting."""
    # secrets.token_hex(8) -> 16 lowercase hex chars; we upper-case for
    # easier reading on stickers/handouts.
    return secrets.token_hex(8).upper()


# Path to the version manifest bundled with static files
_VERSIONS_PATH = os.path.join(os.path.dirname(__file__), 'static', 'clients', 'client_versions.json')


def _load_versions():
    """Load client_versions.json from disk (not cached — admins can update it live).
    Tolerates a UTF-8 BOM (sometimes added when admins edit the file with
    PowerShell or Notepad on Windows)."""
    try:
        with open(_VERSIONS_PATH, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except Exception:
        return {}

registration_bp = Blueprint('registration', __name__)


@registration_bp.route('/api/version', methods=['GET'])
def api_version():
    """
    Returns the latest client version info.
    Called by native clients on startup to check for updates.
    The `url` values are relative — clients must prepend their saved server URL.
    """
    data = _load_versions()
    return jsonify(data)


@registration_bp.route('/api/register/domains', methods=['GET'])
@login_required
def api_register_domains():
    """Authenticated, scoped list of domains for admin tooling.

    This used to be public so native clients could populate a "select your
    tenant" dropdown, but that let any device on the network enumerate
    every tenant on the server. Devices now authenticate with an
    enrollment code (which resolves the tenant server-side), so the public
    listing is no longer needed and has been removed.

    Superadmins see every active domain; everyone else sees only domains
    they hold a role in.
    """
    with bypass_tenant_filter():
        if _is_superadmin():
            rows = (Domain.query
                    .filter_by(is_active=True)
                    .order_by(Domain.name.asc())
                    .all())
        else:
            from models import UserDomainRole
            rows = (db.session.query(Domain)
                    .join(UserDomainRole, UserDomainRole.domain_id == Domain.id)
                    .filter(UserDomainRole.user_id == current_user.id,
                            Domain.is_active == True)
                    .distinct()
                    .order_by(Domain.name.asc())
                    .all())
    return jsonify({
        'status': 'success',
        'domains': [
            {
                'id':   d.id,
                'slug': d.slug,
                'name': d.name,
            } for d in rows
        ]
    })


def _administrable_domains_for_enrollment():
    """Tenants the current user may view/rotate enrollment codes for."""
    with bypass_tenant_filter():
        if _is_superadmin():
            rows = (Domain.query.filter_by(is_active=True)
                    .order_by(Domain.name.asc()).all())
        else:
            rows = []
            for d in Domain.query.filter_by(is_active=True).order_by(Domain.name.asc()).all():
                if has_permission(current_user, 'domain.admin', domain_id=d.id):
                    rows.append(d)
    return [{'id': d.id, 'name': d.name, 'slug': d.slug} for d in rows]


@registration_bp.route('/admin/enrollment')
@login_required
def admin_enrollment_page():
    """Manage per-tenant device enrollment codes (domain admins + superadmin)."""
    domains = _administrable_domains_for_enrollment()
    if not domains:
        abort(403)
    default_id = request.args.get('domain_id', type=int)
    if default_id is None or not any(d['id'] == default_id for d in domains):
        default_id = domains[0]['id']
    return render_template(
        'admin_enrollment.html',
        domains=domains,
        default_domain_id=default_id,
        is_superadmin=_is_superadmin(),
        request_access_path='/request-access',
    )


# ── Per-tenant enrollment code management ────────────────────────────────────

@registration_bp.route('/api/domains/<int:domain_id>/enrollment', methods=['GET'])
@login_required
def api_get_enrollment(domain_id):
    """Return the current enrollment code state for a domain. Visible to
    superadmins and the domain's own admins."""
    if not _can_admin_enrollment(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    with bypass_tenant_filter():
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'not found'}), 404
        return jsonify({
            'status':  'success',
            'enabled': bool(d.enrollment_enabled),
            'code':    d.enrollment_code,
            'expires_at': d.enrollment_code_expires_at.isoformat()
                          if d.enrollment_code_expires_at else None,
            'is_open': d.enrollment_is_open(),
        })


@registration_bp.route('/api/domains/<int:domain_id>/enrollment', methods=['POST'])
@login_required
def api_set_enrollment(domain_id):
    """Mint, rotate, revoke, or toggle a domain's enrollment code.

    Body fields (all optional):
      action:    'rotate' | 'revoke' | 'enable' | 'disable'
                 'rotate' generates a new code (also implicitly enables).
                 'revoke' clears the code (devices can no longer enroll
                          even if enrollment_enabled is True).
      expires_at_iso: optional ISO-8601 datetime (UTC) for code expiry.
                      Pass null to clear.
    """
    if not _can_admin_enrollment(domain_id):
        return jsonify({'status': 'error', 'message': 'forbidden'}), 403
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip().lower()

    with bypass_tenant_filter():
        d = db.session.get(Domain, domain_id)
        if d is None:
            return jsonify({'status': 'error', 'message': 'not found'}), 404

        if action == 'rotate':
            d.enrollment_code = _generate_code()
            d.enrollment_enabled = True
        elif action == 'revoke':
            d.enrollment_code = None
        elif action == 'enable':
            d.enrollment_enabled = True
        elif action == 'disable':
            d.enrollment_enabled = False

        if 'expires_at_iso' in data:
            raw = data.get('expires_at_iso')
            if raw in (None, ''):
                d.enrollment_code_expires_at = None
            else:
                try:
                    # Accept 'Z' suffix from JS toISOString().
                    d.enrollment_code_expires_at = datetime.fromisoformat(
                        str(raw).replace('Z', '+00:00')).replace(tzinfo=None)
                except (TypeError, ValueError):
                    return jsonify({'status': 'error',
                                    'message': 'expires_at_iso must be ISO-8601'}), 400

        db.session.commit()

        return jsonify({
            'status':  'success',
            'enabled': bool(d.enrollment_enabled),
            'code':    d.enrollment_code,
            'expires_at': d.enrollment_code_expires_at.isoformat()
                          if d.enrollment_code_expires_at else None,
            'is_open': d.enrollment_is_open(),
        })


@registration_bp.route('/api/version/update', methods=['POST'])
@login_required
def api_version_update():
    """Admin saves an updated client_versions.json manifest."""
    data = request.get_json(silent=True)
    if not data:
        return jsonify({'status': 'error', 'message': 'No JSON body'}), 400
    try:
        with open(_VERSIONS_PATH, 'w') as f:
            json.dump(data, f, indent=2)
        return jsonify({'status': 'success'})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500


@registration_bp.route('/api/register', methods=['POST'])
@limit_per_ip('register', settings_key='ratelimit.register_per_min')
def api_register():
    """Called by a native client to request registration on this server.

    Devices must present an `enrollment_code` issued by the target tenant.
    The code resolves the domain server-side, so a hostile device cannot
    pick which tenant it lands in by guessing slugs/IDs. Failures return a
    generic 400 (`_BAD_CODE_MSG`) so the endpoint can't be used to
    enumerate tenants or test codes.
    """
    data = request.get_json(silent=True) or {}
    device_id = (data.get('device_id') or '').strip()
    if not device_id:
        return jsonify({'status': 'error', 'message': 'device_id is required'}), 400

    domain = _resolve_domain_from_code(data.get('enrollment_code'))
    if domain is None:
        return jsonify({'status': 'error', 'message': _BAD_CODE_MSG}), 400
    claim_did = domain.id
    code_used = _normalize_code(data.get('enrollment_code'))
    ua = request.headers.get('User-Agent', '')[:255]

    # /api/register is unauthenticated and runs with no tenant context, so
    # the auto-injected tenant filter would hide every PendingDisplay row
    # (current_domain_id() is None ⇒ filter becomes domain_id == -1).
    # Bypass it for the lookups below; we still scope writes to claim_did.
    with bypass_tenant_filter():
        pending = PendingDisplay.query.filter_by(device_id=device_id).first()

        if pending:
            if pending.status == 'approved' and pending.display:
                return jsonify({'status': 'approved', 'token': pending.display.api_key})
            if pending.status == 'approved' and not pending.display:
                # Display was deleted after approval — reset so admin can re-approve
                pending.status = 'pending'
                pending.display_id = None
            if pending.status == 'declined':
                # Allow re-registration after decline by resetting to pending
                pending.status = 'pending'
            # Update metadata
            pending.friendly_name = data.get('friendly_name', pending.friendly_name)
            pending.hostname = data.get('hostname', pending.hostname)
            pending.os = data.get('os', pending.os)
            pending.resolution = data.get('resolution', pending.resolution)
            pending.app_version = data.get('app_version', pending.app_version)
            pending.ip_address = _client_ip()
            pending.user_agent = ua
            pending.enrollment_code_used = code_used
            # The code may resolve to a different tenant on a re-register
            # (admin rotated the device to a new domain); honour that while
            # the row is still pending.
            if pending.status == 'pending':
                pending.domain_id = claim_did
            db.session.commit()
            return jsonify({'status': 'pending'})

        # Per-domain queue cap: refuse new pendings once the tenant already
        # has too many awaiting approval. Existing-device updates above are
        # exempt so a legitimate device that's still polling can refresh.
        pending_count = PendingDisplay.query.filter_by(
            domain_id=claim_did, status='pending').count()
        if pending_count >= _MAX_PENDING_PER_DOMAIN:
            return jsonify({'status': 'error',
                            'message': 'Too many pending registrations for this tenant'}), 429

        pending = PendingDisplay(
            device_id=device_id,
            friendly_name=data.get('friendly_name') or device_id,
            hostname=data.get('hostname'),
            os=data.get('os'),
            resolution=data.get('resolution'),
            app_version=data.get('app_version'),
            ip_address=_client_ip(),
            user_agent=ua,
            domain_id=claim_did,
            enrollment_code_used=code_used,
        )
        db.session.add(pending)
        db.session.commit()
        return jsonify({'status': 'pending'}), 201


@registration_bp.route('/api/register/status/<device_id>', methods=['GET'])
def api_register_status(device_id):
    """Polled every 5 s by the client to check approval status."""
    # Unauthenticated endpoint — no tenant context, so the auto-injected
    # tenant filter would always return None and the client would hang on
    # "Awaiting Approval" forever. Bypass it; device_id is unique globally.
    with bypass_tenant_filter():
        pending = PendingDisplay.query.filter_by(device_id=device_id).first()
        if not pending:
            return jsonify({'status': 'error', 'message': 'Unknown device_id'}), 404

        if pending.status == 'approved' and pending.display:
            return jsonify({'status': 'approved', 'token': pending.display.api_key,
                            'device_id': device_id})
        if pending.status == 'approved' and not pending.display:
            # Orphaned — display was deleted; reset to pending so client re-registers
            pending.status = 'pending'
            db.session.commit()
            return jsonify({'status': 'pending'})
        if pending.status == 'declined':
            return jsonify({'status': 'declined'})
        return jsonify({'status': 'pending'})


@registration_bp.route('/api/register/<int:pending_id>/approve', methods=['POST'])
@login_required
def api_register_approve(pending_id):
    """Admin approves a pending display — creates the Display record.

    Tenant guard: a pending row that declared a domain (pending.domain_id)
    can only be approved by a member of that tenant or by a superadmin. A
    pending row with no tenant claim can only be approved by a superadmin
    (they decide which domain it belongs to via the active session domain).
    """
    pending = PendingDisplay.query.get_or_404(pending_id)
    if pending.status != 'pending':
        return jsonify({'status': 'error', 'message': 'Not in pending state'}), 400

    active_did = current_domain_id()
    if pending.domain_id is not None:
        if not _is_superadmin() and pending.domain_id != active_did:
            return jsonify({'status': 'error',
                            'message': 'This device is registered to another tenant'}), 403
        target_did = pending.domain_id
    else:
        # Unscoped registration — only superadmins can route it, into their
        # currently-active tenant.
        if not _is_superadmin():
            return jsonify({'status': 'error',
                            'message': 'Device did not declare a tenant; ask a superadmin to approve'}), 403
        if active_did is None:
            return jsonify({'status': 'error',
                            'message': 'Switch to the target tenant before approving an unscoped device'}), 400
        target_did = active_did

    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip() or pending.friendly_name or pending.device_id
    group_id = data.get('group_id')
    if group_id is not None:
        try:
            group_id = int(group_id) if group_id != '' else None
        except (TypeError, ValueError):
            group_id = None

    # Auto-generate a 4-digit unlock PIN so newly-registered displays are
    # locked-by-default. Admin sees and can change/clear the PIN from the
    # edit-display form.
    import random as _rand
    pin = ''.join(str(_rand.randint(0, 9)) for _ in range(4))

    # Check for existing Display with this device_id (may be orphaned from a previous approval)
    from models import Display
    with bypass_tenant_filter():
        existing = Display.query.filter_by(device_id=pending.device_id).first()
        if existing:
            # Option 1: Re-approve (update) the existing Display
            existing.name = name
            existing.domain_id = target_did
            existing.group_id = group_id
            existing.unlock_pin = pin
            db.session.flush()
            pending.status = 'approved'
            pending.display_id = existing.id
            pending.approved_domain_id = target_did
            db.session.commit()
            return jsonify({'status': 'success', 'token': existing.api_key, 'display_id': existing.id})

        # Option 2: No existing Display, create new
        display = Display(
            name=name,
            device_id=pending.device_id,
            api_key=str(uuid.uuid4()),
            location=data.get('location', ''),
            description='',
            group_id=group_id,
            unlock_pin=pin,
            domain_id=target_did,
        )
        db.session.add(display)
        db.session.flush()  # get display.id before commit

        pending.status = 'approved'
        pending.display_id = display.id
        pending.approved_domain_id = target_did
        db.session.commit()

    return jsonify({'status': 'success', 'token': display.api_key, 'display_id': display.id})


@registration_bp.route('/api/register/<int:pending_id>/decline', methods=['POST'])
@login_required
def api_register_decline(pending_id):
    """Admin declines a pending display."""
    pending = PendingDisplay.query.get_or_404(pending_id)
    if pending.status != 'pending':
        return jsonify({'status': 'error', 'message': 'Not in pending state'}), 400
    # Same tenant guard as approve.
    active_did = current_domain_id()
    if pending.domain_id is not None:
        if not _is_superadmin() and pending.domain_id != active_did:
            return jsonify({'status': 'error',
                            'message': 'This device is registered to another tenant'}), 403
    else:
        if not _is_superadmin():
            return jsonify({'status': 'error',
                            'message': 'Device did not declare a tenant; ask a superadmin to decline'}), 403
    pending.status = 'declined'
    db.session.commit()
    return jsonify({'status': 'success'})


@registration_bp.route('/api/register/bulk-approve', methods=['POST'])
@login_required
def api_register_bulk_approve():
    """Approve many pending registrations in one call.

    Body: {"ids": [int,...], "group_id": <int|null>}

    Returns per-ID outcome. group_id is applied to every approval; name and
    location come from each pending row's friendly_name (no per-row override
    in bulk mode -- admins can rename later from the displays page).
    """
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400

    group_id = data.get('group_id')
    if group_id in ('', None):
        group_id = None
    else:
        try:
            group_id = int(group_id)
        except (TypeError, ValueError):
            group_id = None

    active_did = current_domain_id()
    is_super = _is_superadmin()
    results = []
    approved = 0
    for pid in ids:
        pending = PendingDisplay.query.get(pid)
        if pending is None:
            results.append({'id': pid, 'ok': False, 'error': 'not found'}); continue
        if pending.status != 'pending':
            results.append({'id': pid, 'ok': False, 'error': 'not in pending state'}); continue
        if pending.domain_id is not None:
            if not is_super and pending.domain_id != active_did:
                results.append({'id': pid, 'ok': False, 'error': 'wrong tenant'}); continue
            target_did = pending.domain_id
        else:
            if not is_super:
                results.append({'id': pid, 'ok': False, 'error': 'unscoped device — superadmin only'}); continue
            if active_did is None:
                results.append({'id': pid, 'ok': False, 'error': 'no active tenant'}); continue
            target_did = active_did

        import random as _rand
        pin = ''.join(str(_rand.randint(0, 9)) for _ in range(4))
        name = pending.friendly_name or pending.device_id

        with bypass_tenant_filter():
            existing = Display.query.filter_by(device_id=pending.device_id).first()
            if existing:
                existing.name = name
                existing.domain_id = target_did
                existing.group_id = group_id
                existing.unlock_pin = pin
                db.session.flush()
                pending.status = 'approved'
                pending.display_id = existing.id
                pending.approved_domain_id = target_did
            else:
                display = Display(
                    name=name,
                    device_id=pending.device_id,
                    api_key=str(uuid.uuid4()),
                    location='',
                    description='',
                    group_id=group_id,
                    unlock_pin=pin,
                    domain_id=target_did,
                )
                db.session.add(display)
                db.session.flush()
                pending.status = 'approved'
                pending.display_id = display.id
                pending.approved_domain_id = target_did
        approved += 1
        results.append({'id': pid, 'ok': True})
    db.session.commit()
    return jsonify({'status': 'success', 'approved': approved,
                    'requested': len(ids), 'results': results})


@registration_bp.route('/api/register/bulk-decline', methods=['POST'])
@login_required
def api_register_bulk_decline():
    """Decline many pending registrations in one call.

    Body: {"ids": [int,...]}
    """
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400

    active_did = current_domain_id()
    is_super = _is_superadmin()
    declined = 0
    results = []
    for pid in ids:
        pending = PendingDisplay.query.get(pid)
        if pending is None:
            results.append({'id': pid, 'ok': False, 'error': 'not found'}); continue
        if pending.status != 'pending':
            results.append({'id': pid, 'ok': False, 'error': 'not in pending state'}); continue
        if pending.domain_id is not None:
            if not is_super and pending.domain_id != active_did:
                results.append({'id': pid, 'ok': False, 'error': 'wrong tenant'}); continue
        else:
            if not is_super:
                results.append({'id': pid, 'ok': False, 'error': 'unscoped device — superadmin only'}); continue
        pending.status = 'declined'
        declined += 1
        results.append({'id': pid, 'ok': True})
    db.session.commit()
    return jsonify({'status': 'success', 'declined': declined,
                    'requested': len(ids), 'results': results})


@registration_bp.route('/api/register/pending', methods=['GET'])
@login_required
def api_pending_list():
    """Returns pending registrations the caller is allowed to see.

    Filtering rules:
      - Regular users see only pending rows whose declared domain matches
        their currently-active session tenant.
      - Superadmins see everything by default; pass ?scope=tenant to
        restrict the list to the active tenant.
    """
    q = PendingDisplay.query.filter_by(status='pending')
    scope = (request.args.get('scope') or '').strip().lower()
    if _is_superadmin() and scope != 'tenant':
        pass  # superadmin global view
    else:
        active_did = current_domain_id()
        # Members of a tenant only see devices that explicitly claimed their
        # tenant. Unscoped pendings are reserved for superadmin routing.
        if active_did is None:
            q = q.filter(PendingDisplay.id == -1)  # nothing
        else:
            q = q.filter(PendingDisplay.domain_id == active_did)
    items = q.order_by(PendingDisplay.requested_at.desc()).all()
    return jsonify({'status': 'success', 'pending': [p.to_dict() for p in items]})


# ── Browser-based self-service registration ───────────────────────────────────

@registration_bp.route('/request-access', methods=['GET'])
def request_access_page():
    """Public page — anyone with a valid enrollment code can request
    browser-based display access.

    Accepts ?code=<enrollment_code> so an admin can hand out a per-tenant
    URL that pre-fills the code field. The code is resolved server-side
    before the slug is exposed to the template — invalid codes render the
    page with no pre-fill (and the user must type it in manually).
    """
    raw = request.args.get('code') or ''
    domain_slug = ''
    domain_name = ''
    prefilled_code = ''
    d = _resolve_domain_from_code(raw)
    if d is not None:
        domain_slug = d.slug
        domain_name = d.name
        prefilled_code = _normalize_code(raw)
    return render_template('request_access.html',
                           domain_slug=domain_slug,
                           domain_name=domain_name,
                           prefilled_code=prefilled_code)


@registration_bp.route('/api/register/browser', methods=['POST'])
@limit_per_ip('browser_register', settings_key='ratelimit.browser_register_per_min')
def api_register_browser():
    """
    Browser equivalent of /api/register.
    Accepts a JSON or form body with friendly_name + enrollment_code.
    Generates its own device_id server-side (stored client-side in
    localStorage) so the browser has nothing to install.
    """
    data = request.get_json(silent=True) or request.form
    friendly_name = (data.get('friendly_name') or '').strip() or 'Browser Display'

    # Stable browser identity — use a UUID we store in the response and the
    # client keeps in localStorage (sent back on every poll).
    device_id = (data.get('device_id') or '').strip()
    if not device_id:
        device_id = str(uuid.uuid4())

    domain = _resolve_domain_from_code(data.get('enrollment_code'))
    if domain is None:
        return jsonify({'status': 'error', 'message': _BAD_CODE_MSG}), 400
    claim_did = domain.id
    code_used = _normalize_code(data.get('enrollment_code'))
    ua = request.headers.get('User-Agent', '')[:255]

    # Browser onboarding is unauthenticated → bypass tenant filter so we can
    # find/update the pending row for this device_id regardless of session.
    with bypass_tenant_filter():
        pending = PendingDisplay.query.filter_by(device_id=device_id).first()
        if pending:
            if pending.status == 'approved' and pending.display:
                return jsonify({'status': 'approved', 'token': pending.display.api_key, 'device_id': device_id})
            if pending.status == 'declined':
                return jsonify({'status': 'declined', 'device_id': device_id})
            # refresh metadata
            pending.friendly_name = friendly_name
            pending.ip_address = _client_ip()
            pending.user_agent = ua
            pending.enrollment_code_used = code_used
            if pending.status == 'pending':
                pending.domain_id = claim_did
            db.session.commit()
            return jsonify({'status': 'pending', 'device_id': device_id})

        pending_count = PendingDisplay.query.filter_by(
            domain_id=claim_did, status='pending').count()
        if pending_count >= _MAX_PENDING_PER_DOMAIN:
            return jsonify({'status': 'error',
                            'message': 'Too many pending registrations for this tenant'}), 429

        pending = PendingDisplay(
            device_id=device_id,
            friendly_name=friendly_name,
            hostname=(request.headers.get('User-Agent', '') or '')[:80],
            os='Browser',
            resolution=data.get('resolution', ''),
            app_version='web',
            ip_address=_client_ip(),
            user_agent=ua,
            domain_id=claim_did,
            enrollment_code_used=code_used,
        )
        db.session.add(pending)
        db.session.commit()
        return jsonify({'status': 'pending', 'device_id': device_id}), 201


def _client_ip():
    ra = request.remote_addr or ''
    if ra and ra not in ('127.0.0.1', '::1'):
        return ra
    for header in ('CF-Connecting-IP', 'True-Client-IP', 'X-Real-IP'):
        v = request.headers.get(header)
        if v:
            return v.split(',')[0].strip()
    try:
        if request.access_route:
            return request.access_route[0]
    except Exception:
        pass
    return ra or '127.0.0.1'
