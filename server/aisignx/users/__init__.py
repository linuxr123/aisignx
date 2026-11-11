"""User management (admin only)."""
from flask import Blueprint

bp = Blueprint("users", __name__, url_prefix="/users")

from . import routes  # noqa: E402,F401
