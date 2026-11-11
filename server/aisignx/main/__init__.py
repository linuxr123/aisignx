"""Dashboard and other top-level pages."""
from flask import Blueprint

bp = Blueprint("main", __name__)

from . import routes  # noqa: E402,F401
