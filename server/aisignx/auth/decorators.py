"""Role-based access decorators."""
from functools import wraps

from flask import abort
from flask_login import current_user, login_required


def role_required(minimum: str):
    """Require login and at least the given role (viewer < editor < admin)."""

    def decorator(view):
        @wraps(view)
        @login_required
        def wrapped(*args, **kwargs):
            if not current_user.has_role(minimum):
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def admin_required(view):
    return role_required("admin")(view)


def editor_required(view):
    return role_required("editor")(view)
