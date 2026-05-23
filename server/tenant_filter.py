"""
Tenant query filter - Phase 1, Task 2.

Auto-injects a WHERE domain_id = current_domain_id() filter on every ORM
SELECT against a tenant-scoped model. Models declare themselves tenant-scoped
by inheriting the TenantModel mixin (see models.py). Implementation uses the
SQLAlchemy `do_orm_execute` event hook, which is the supported way to apply
such cross-cutting filters.

Tenant context lives in flask.g (per-request) and is populated by app.py's
before_request hook from session/api-token. Outside an app context (CLI,
background jobs) the filter is bypassed.

Default-deny semantics: if a request IS in progress but no tenant context
has been set, queries on tenant models return no rows. This is intentional;
a missing tenant context is almost always a bug, and we want it to be
visible (no data returned) rather than silent (cross-tenant leak).

Bypass for legitimate cross-tenant code paths:

    with bypass_tenant_filter():
        all_displays = Display.query.all()    # sees every domain

Used by superadmin views, the bootstrap, and CLI tools.
"""
from contextlib import contextmanager

from flask import g, has_app_context
from flask_sqlalchemy.query import Query as _BaseQuery
from sqlalchemy import event
from sqlalchemy.orm import with_loader_criteria, Session


# -----------------------------------------------------------------------------
# Per-request tenant context
# -----------------------------------------------------------------------------
_BYPASS_KEY = '_tenant_filter_bypass'
_DOMAIN_KEY = '_current_domain_id'


def current_domain_id():
    """Return the active tenant's id, or None if not in a request or unset."""
    if not has_app_context():
        return None
    return getattr(g, _DOMAIN_KEY, None)


def set_current_domain_id(domain_id):
    """Set the active tenant for the current request. Called by app.py's
    before_request hook based on the logged-in user's selected domain or
    the API token's domain."""
    if has_app_context():
        setattr(g, _DOMAIN_KEY, int(domain_id) if domain_id is not None else None)


def clear_current_domain_id():
    """Drop the tenant context. Called by after_request."""
    if has_app_context():
        try:
            delattr(g, _DOMAIN_KEY)
        except (AttributeError, RuntimeError):
            pass


def _is_bypassed():
    if not has_app_context():
        return True   # outside request context, bypass is the default
    return bool(getattr(g, _BYPASS_KEY, False))


@contextmanager
def bypass_tenant_filter():
    """Temporarily disable tenant filtering. Use for legitimate cross-tenant
    work like superadmin domain listings or housekeeping jobs."""
    if not has_app_context():
        yield
        return
    prev = getattr(g, _BYPASS_KEY, False)
    setattr(g, _BYPASS_KEY, True)
    try:
        yield
    finally:
        setattr(g, _BYPASS_KEY, prev)


# -----------------------------------------------------------------------------
# Query subclass (kept as marker so model.query is a TenantQuery; actual
# filtering happens via the do_orm_execute event below).
# -----------------------------------------------------------------------------
class TenantQuery(_BaseQuery):
    """Marker subclass. Tenant filtering is applied by the do_orm_execute
    listener registered in install_tenant_filter()."""
    pass


# -----------------------------------------------------------------------------
# Event-based filter installation
# -----------------------------------------------------------------------------
_INSTALLED = False


def install_tenant_filter(db):
    """Register the do_orm_execute listener. Call once at app startup, after
    db.init_app(app)."""
    global _INSTALLED
    if _INSTALLED:
        return

    # Collect every mapped class that mixes in TenantModel. We do this once
    # at install time; new models registered after install won't be auto-
    # filtered (which would only matter for hot-reload during dev).
    from models import TenantModel
    tenant_classes = []
    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if isinstance(cls, type) and issubclass(cls, TenantModel) and hasattr(cls, 'domain_id'):
            tenant_classes.append(cls)

    @event.listens_for(Session, 'do_orm_execute')
    def _add_tenant_filter(execute_state):
        if not execute_state.is_select:
            return
        if execute_state.is_column_load or execute_state.is_relationship_load:
            return
        if _is_bypassed():
            return

        did = current_domain_id()

        for tcls in tenant_classes:
            # Use direct expression form (not lambda) so the criterion is
            # evaluated once per call with the current did, and SQLAlchemy
            # doesn't cache a stale value across requests.
            if did is None:
                expr = tcls.domain_id == -1
            else:
                expr = tcls.domain_id == did
            execute_state.statement = execute_state.statement.options(
                with_loader_criteria(tcls, expr, include_aliases=True)
            )

    # ---- Auto-stamp domain_id on insert ------------------------------------
    # Defense in depth: any tenant row added to the session without an
    # explicit domain_id gets stamped with current_domain_id(). Two reasons:
    #   1. Saves every route from having to remember to set it.
    #   2. Prevents accidental cross-tenant inserts (a row created with a
    #      hard-coded domain_id different from the request's tenant context
    #      will trip the cross-tenant guard below).
    @event.listens_for(Session, 'before_flush')
    def _stamp_tenant_on_insert(session, flush_context, instances):
        if _is_bypassed():
            return
        did = current_domain_id()
        for obj in session.new:
            if not isinstance(obj, tuple(tenant_classes)):
                continue
            existing = getattr(obj, 'domain_id', None)
            if existing is None:
                if did is None:
                    # No tenant context AND no explicit domain_id is a bug;
                    # raise rather than silently writing -1 or NULL.
                    raise RuntimeError(
                        f'Refusing to insert {type(obj).__name__} with no '
                        'domain_id and no tenant context. Either set '
                        'domain_id explicitly or run inside a request that '
                        'has tenant context.'
                    )
                obj.domain_id = did
            elif did is not None and int(existing) != int(did):
                # Cross-tenant write attempt -- block it.
                raise RuntimeError(
                    f'Cross-tenant insert blocked: {type(obj).__name__}.'
                    f'domain_id={existing} but current tenant is {did}. '
                    'Use bypass_tenant_filter() if this is intentional '
                    '(e.g. superadmin tooling).'
                )

    _INSTALLED = True



