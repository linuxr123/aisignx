"""Organization settings (admin only)."""
from flask import Blueprint

bp = Blueprint("settings", __name__, url_prefix="/settings")

from . import routes  # noqa: E402,F401
