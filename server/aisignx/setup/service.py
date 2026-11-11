"""First-run detection: setup is required until at least one admin exists."""
from flask import current_app

from ..extensions import db
from ..models import User
from ..models.user import ROLE_ADMIN

_DONE_KEY = "aisignx_setup_done"


def setup_required() -> bool:
    # Once setup completes it never reverts, so cache the result per app.
    if current_app.extensions.get(_DONE_KEY):
        return False
    has_admin = db.session.query(User.id).filter_by(role=ROLE_ADMIN).first() is not None
    if has_admin:
        current_app.extensions[_DONE_KEY] = True
    return not has_admin
