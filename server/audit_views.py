"""

Audit log viewer - Phase 1 follow-up.



Read-only UI + JSON API for the AuditLog table. Filtering by action,

target_type, actor, and date range. Superadmins can view all tenants

(?scope=all) or filter by tenant; domain admins only see their tenant(s).



Routes

------

GET  /audit                  - HTML viewer page (paginated, with filters)

GET  /api/audit              - JSON list endpoint with the same filters

GET  /api/audit/<id>         - single audit row (raw payload, sandbox-checked)

GET  /api/audit/actions      - distinct action keys, for the filter dropdown

"""

from datetime import datetime, timedelta



from flask import Blueprint, render_template, request, jsonify, abort, Response

from flask_login import login_required

from sqlalchemy import desc



from models import AuditLog, User, Domain, db

from permissions import require_permission

from tenant_filter import bypass_tenant_filter, current_domain_id





audit_bp = Blueprint('audit', __name__)





_PAGE_SIZE_DEFAULT = 50

_PAGE_SIZE_MAX     = 200





def _is_superadmin():

    from flask_login import current_user

    return getattr(current_user, 'is_superadmin', False)





def _audit_scope_domain_ids(scope_all: bool):

    """Domain ids the current user may view in the audit log.



    None means no forced tenant filter (superadmin global view).

    """

    if scope_all and _is_superadmin():

        return None

    if _is_superadmin():

        did = current_domain_id()

        return [did] if did else None

    from admin import _administrable_domain_ids

    return _administrable_domain_ids()





def _apply_audit_domain_filter(q, scope_all: bool):

    """Restrict AuditLog rows by tenant. AuditLog is not tenant-filtered automatically."""

    scope_ids = _audit_scope_domain_ids(scope_all)

    domain_id_param = request.args.get('domain_id', type=int)



    if scope_all and _is_superadmin():

        if domain_id_param:

            return q.filter(AuditLog.domain_id == domain_id_param)

        return q



    if scope_ids is None:

        if domain_id_param:

            return q.filter(AuditLog.domain_id == domain_id_param)

        return q



    if not scope_ids:

        return q.filter(AuditLog.domain_id == -1)



    if domain_id_param is not None:

        if domain_id_param not in scope_ids:

            return q.filter(AuditLog.domain_id == -1)

        return q.filter(AuditLog.domain_id == domain_id_param)



    if len(scope_ids) == 1:

        return q.filter(AuditLog.domain_id == scope_ids[0])

    return q.filter(AuditLog.domain_id.in_(scope_ids))





def _parse_iso(value):

    """Parse an ISO date or datetime string. Returns None on failure."""

    if not value:

        return None

    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%dT%H:%M', '%Y-%m-%d'):

        try:

            return datetime.strptime(value, fmt)

        except ValueError:

            continue

    return None





def _build_query(scope_all: bool):

    """Build a filtered AuditLog query (caller must use bypass_tenant_filter)."""

    args = request.args

    q = AuditLog.query



    action       = (args.get('action') or '').strip()

    target_type  = (args.get('target_type') or '').strip()

    actor_user   = args.get('actor_user_id', type=int)

    since        = _parse_iso(args.get('since'))

    until        = _parse_iso(args.get('until'))



    if action:

        q = q.filter(AuditLog.action == action)

    if target_type == '__display__':

        q = q.filter(AuditLog.target_type.in_(('display', 'displays', 'display_group')))

    elif target_type:

        q = q.filter(AuditLog.target_type == target_type)

    if actor_user:

        q = q.filter(AuditLog.actor_user_id == actor_user)

    if since:

        q = q.filter(AuditLog.timestamp >= since)

    if until:

        q = q.filter(AuditLog.timestamp <= until)



    q = _apply_audit_domain_filter(q, scope_all)

    return q.order_by(desc(AuditLog.timestamp))





def _audit_domains_for_filter():

    """Tenants shown in the audit page tenant dropdown."""

    if _is_superadmin():

        with bypass_tenant_filter():

            return Domain.query.order_by(Domain.name.asc()).all()

    from admin import _administrable_domain_ids

    ids = _administrable_domain_ids()

    if not ids:

        return []

    with bypass_tenant_filter():

        return Domain.query.filter(Domain.id.in_(ids)).order_by(Domain.name.asc()).all()





def _can_view_audit_entry(row):

    if row is None:

        return False

    if _is_superadmin():

        return True

    if row.domain_id is None:

        return False

    from admin import _administrable_domain_ids

    return row.domain_id in _administrable_domain_ids()





@audit_bp.route('/audit')

@login_required

@require_permission('audit.read')

def audit_page():

    """Render the audit log viewer page. JS hits /api/audit for data."""

    domains = _audit_domains_for_filter()

    show_tenant_filter = _is_superadmin() or len(domains) > 1

    return render_template('audit.html', is_superadmin=_is_superadmin(),

                           domains=domains, show_tenant_filter=show_tenant_filter)





@audit_bp.route('/api/audit')

@login_required

@require_permission('audit.read')

def api_audit_list():

    """Paginated JSON listing of audit entries.



    Query params:

      action          exact match

      target_type     exact match

      actor_user_id   exact match

      domain_id       exact match, superadmin scope=all only

      since           ISO date or datetime; inclusive

      until           ISO date or datetime; inclusive

      scope           'all' (superadmin only) for cross-tenant view

      page            1-indexed; default 1

      page_size       default 50, max 200

    """

    scope_all = (request.args.get('scope') == 'all') and _is_superadmin()

    page      = max(1, request.args.get('page', 1, type=int))

    page_size = min(_PAGE_SIZE_MAX,

                    max(1, request.args.get('page_size', _PAGE_SIZE_DEFAULT, type=int)))



    with bypass_tenant_filter():

        q = _build_query(scope_all)

        total = q.count()

        rows = q.offset((page - 1) * page_size).limit(page_size).all()

        actor_ids = {r.actor_user_id for r in rows if r.actor_user_id}

        users = ({u.id: u.username for u in

                  User.query.filter(User.id.in_(actor_ids)).all()}

                 if actor_ids else {})

        domain_ids = {r.domain_id for r in rows if r.domain_id}

        domains = ({d.id: d.name for d in

                    Domain.query.filter(Domain.id.in_(domain_ids)).all()}

                   if domain_ids else {})



    def _row_dict(r):

        return {

            'id':          r.id,

            'timestamp':   r.timestamp.isoformat() if r.timestamp else None,

            'action':      r.action,

            'target_type': r.target_type,

            'target_id':   r.target_id,

            'actor':       users.get(r.actor_user_id),

            'actor_id':    r.actor_user_id,

            'token_id':    r.actor_api_token_id,

            'ip':          r.ip_address,

            'domain_id':   r.domain_id,

            'domain':      domains.get(r.domain_id),

            'has_payload': bool(r.payload),

        }



    return jsonify({

        'status':    'success',

        'total':     total,

        'page':      page,

        'page_size': page_size,

        'pages':     (total + page_size - 1) // page_size,

        'scope':     'all' if scope_all else 'tenant',

        'entries':   [_row_dict(r) for r in rows],

    })





@audit_bp.route('/api/audit/<int:entry_id>')

@login_required

@require_permission('audit.read')

def api_audit_detail(entry_id):

    """Return one audit entry including its full payload."""

    with bypass_tenant_filter():

        row = db.session.get(AuditLog, entry_id)



    if not _can_view_audit_entry(row):

        return jsonify({'status': 'error', 'message': 'not found'}), 404



    actor_username = None

    if row.actor_user_id:

        with bypass_tenant_filter():

            u = db.session.get(User, row.actor_user_id)

            actor_username = u.username if u else None



    return jsonify({

        'status': 'success',

        'entry': {

            'id':          row.id,

            'timestamp':   row.timestamp.isoformat() if row.timestamp else None,

            'action':      row.action,

            'target_type': row.target_type,

            'target_id':   row.target_id,

            'actor':       actor_username,

            'actor_id':    row.actor_user_id,

            'token_id':    row.actor_api_token_id,

            'ip':          row.ip_address,

            'user_agent':  row.user_agent,

            'domain_id':   row.domain_id,

            'payload':     row.payload,

        },

    })





@audit_bp.route('/api/audit/actions')

@login_required

@require_permission('audit.read')

def api_audit_actions():

    """Distinct action keys present in the log. Used to populate the

    filter dropdown. Limited to the user's tenant unless scope=all."""

    scope_all = (request.args.get('scope') == 'all') and _is_superadmin()

    with bypass_tenant_filter():

        q = _build_query(scope_all)

        rows = q.with_entities(AuditLog.action).distinct().all()

    return jsonify({

        'status': 'success',

        'actions': sorted(r[0] for r in rows if r[0]),

    })





# ---------------------------------------------------------------------------

# Export: stream the filtered audit set as CSV or JSON. Same query params as

# /api/audit (action, target_type, actor_user_id, since, until, scope=all).

# Hard-capped at _EXPORT_MAX rows so a careless click can't OOM the server.

# ---------------------------------------------------------------------------

_EXPORT_MAX = 50_000





def _export_iter_csv(query, users, domains, scope_all):

    import csv, io

    header = ['id', 'timestamp', 'action', 'target_type', 'target_id',

              'actor_id', 'actor', 'token_id', 'ip', 'domain_id', 'domain',

              'payload']

    buf = io.StringIO()

    w = csv.writer(buf)

    w.writerow(header)

    yield buf.getvalue()

    buf.seek(0); buf.truncate(0)



    for r in query.yield_per(500).limit(_EXPORT_MAX):

        w.writerow([

            r.id,

            r.timestamp.isoformat() if r.timestamp else '',

            r.action or '',

            r.target_type or '',

            r.target_id if r.target_id is not None else '',

            r.actor_user_id or '',

            users.get(r.actor_user_id, '') or '',

            r.actor_api_token_id or '',

            r.ip_address or '',

            r.domain_id or '',

            domains.get(r.domain_id) or '',

            (str(r.payload) if r.payload else ''),

        ])

        chunk = buf.getvalue()

        if chunk:

            yield chunk

            buf.seek(0); buf.truncate(0)





@audit_bp.route('/api/audit/export')

@login_required

@require_permission('audit.read')

def api_audit_export():

    """Download the filtered audit log as CSV (default) or JSON.



    Same filters as /api/audit. ``format=json`` returns a JSON array; any

    other value returns CSV. Capped at 50,000 rows per request."""

    scope_all = (request.args.get('scope') == 'all') and _is_superadmin()

    fmt       = (request.args.get('format') or 'csv').lower()

    ts        = datetime.utcnow().strftime('%Y%m%d-%H%M%S')



    with bypass_tenant_filter():

        q = _build_query(scope_all)

        actor_ids = {aid for (aid,) in q.with_entities(AuditLog.actor_user_id)

                                   .distinct().all()

                     if aid}

        users = ({u.id: u.username for u in

                  User.query.filter(User.id.in_(actor_ids)).all()}

                 if actor_ids else {})

        domain_ids = {did for (did,) in q.with_entities(AuditLog.domain_id)

                                    .distinct().all()

                      if did}

        domains = ({d.id: d.name for d in

                    Domain.query.filter(Domain.id.in_(domain_ids)).all()}

                   if domain_ids else {})

        rows = q.limit(_EXPORT_MAX).all() if fmt == 'json' else None



    if fmt == 'json':

        out = [{

            'id':          r.id,

            'timestamp':   r.timestamp.isoformat() if r.timestamp else None,

            'action':      r.action,

            'target_type': r.target_type,

            'target_id':   r.target_id,

            'actor_id':    r.actor_user_id,

            'actor':       users.get(r.actor_user_id),

            'token_id':    r.actor_api_token_id,

            'ip':          r.ip_address,

            'domain_id':   r.domain_id,

            'domain':      domains.get(r.domain_id),

            'payload':     r.payload,

        } for r in rows]

        resp = jsonify(out)

        resp.headers['Content-Disposition'] = (

            f'attachment; filename="audit-{ts}.json"')

        return resp



    resp = Response(_export_iter_csv(q, users, domains, scope_all),

                    mimetype='text/csv')

    resp.headers['Content-Disposition'] = (

        f'attachment; filename="audit-{ts}.csv"')

    return resp

