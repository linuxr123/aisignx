"""Update check: is a newer AiSignX release published on GitHub? (Admin page and banner.)"""
from flask import Blueprint

bp = Blueprint("updates", __name__, url_prefix="/updates")

from . import routes  # noqa: E402,F401
