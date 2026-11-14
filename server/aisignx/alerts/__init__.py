"""Alerts: problems detected by the background sweep, with optional e-mail."""
from flask import Blueprint

bp = Blueprint("alerts", __name__, url_prefix="/alerts")

from . import routes  # noqa: E402,F401
