"""
Tenant-scoped user accounts.

Each interactive user belongs to one tenant (home_domain_id). Usernames and
emails are unique *within* that tenant, so different tenants may each have
their own "admin" user.

Superadmins use home_domain_id=NULL and sign in with an empty organization code.
"""
from __future__ import annotations

from sqlalchemy import func

from bootstrap import default_tenant_domain_id
from models import User, Domain, UserDomainRole, db
from tenant_filter import bypass_tenant_filter


def normalize_tenant_slug(raw: str | None) -> str:
    return (raw or '').strip().lower()


def resolve_domain_by_login_slug(slug: str):
    """Active tenant from organization code (domain slug). None if not found."""
    slug = normalize_tenant_slug(slug)
    if not slug:
        return None
    with bypass_tenant_filter():
        return Domain.query.filter(
            func.lower(Domain.slug) == slug,
            Domain.is_active == True,
        ).first()


def find_user_for_login(tenant_slug: str | None, username: str):
    """Resolve a user row for login without revealing other tenants.

    tenant_slug empty -> superadmin / global accounts (home_domain_id IS NULL).
    tenant_slug set   -> user in that tenant only.
    """
    username = (username or '').strip()
    if not username:
        return None, 'invalid_credentials'

    slug = normalize_tenant_slug(tenant_slug)
    with bypass_tenant_filter():
        if not slug:
            user = (User.query
                    .filter(User.username == username,
                            User.home_domain_id.is_(None),
                            User.is_superadmin == True)
                    .first())
            if user is None:
                return None, 'invalid_credentials'
            return user, None

        domain = resolve_domain_by_login_slug(slug)
        if domain is None:
            return None, 'invalid_tenant'
        user = User.query.filter_by(
            home_domain_id=domain.id,
            username=username,
        ).first()
        if user is None:
            return None, 'invalid_credentials'
        return user, None


def username_taken(home_domain_id: int | None, username: str, exclude_user_id: int | None = None) -> bool:
    username = (username or '').strip()
    if not username:
        return False
    with bypass_tenant_filter():
        q = User.query.filter(User.username == username)
        if home_domain_id is None:
            q = q.filter(User.home_domain_id.is_(None))
        else:
            q = q.filter(User.home_domain_id == home_domain_id)
        if exclude_user_id:
            q = q.filter(User.id != exclude_user_id)
        return q.first() is not None


def email_taken(home_domain_id: int | None, email: str, exclude_user_id: int | None = None) -> bool:
    email = (email or '').strip()
    if not email:
        return False
    with bypass_tenant_filter():
        q = User.query.filter(User.email == email)
        if home_domain_id is None:
            q = q.filter(User.home_domain_id.is_(None))
        else:
            q = q.filter(User.home_domain_id == home_domain_id)
        if exclude_user_id:
            q = q.filter(User.id != exclude_user_id)
        return q.first() is not None


def backfill_home_domain_from_roles():
    """Set home_domain_id from the earliest role assignment when missing."""
    with bypass_tenant_filter():
        users = User.query.filter(User.home_domain_id.is_(None),
                                  User.is_superadmin == False).all()
        for user in users:
            udr = (UserDomainRole.query
                   .filter_by(user_id=user.id)
                   .order_by(UserDomainRole.created_at.asc())
                   .first())
            if udr:
                user.home_domain_id = udr.domain_id
        db.session.commit()


def login_session_domain_id(user) -> int | None:
    """Tenant to activate in session after a successful login."""
    if getattr(user, 'is_superadmin', False):
        return default_tenant_domain_id()
    if user.home_domain_id:
        return user.home_domain_id
    domains = user.domains()
    return domains[0].id if domains else None
