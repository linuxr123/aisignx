"""JSON API for scripts and the PowerPoint add-in (/api/v1/..., plus legacy /api/media).

Authenticated with Bearer API tokens only, so the blueprint is exempt from CSRF.
"""
from flask import Blueprint

bp = Blueprint("api", __name__, url_prefix="/api")

from . import emergency, routes  # noqa: E402,F401
