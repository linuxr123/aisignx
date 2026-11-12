"""Schedules: play playlists on screens at set times and days."""
from flask import Blueprint

bp = Blueprint("schedules", __name__, url_prefix="/schedules")

from . import routes  # noqa: E402,F401
