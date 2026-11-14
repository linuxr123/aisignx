"""Personal API tokens for the JSON API (session pages; the API itself is in api/)."""
from flask import Blueprint

bp = Blueprint("tokens", __name__, url_prefix="/tokens")

from . import routes  # noqa: E402,F401
