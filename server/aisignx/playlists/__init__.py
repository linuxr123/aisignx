"""Playlists: ordered media with durations and transitions."""
from flask import Blueprint

bp = Blueprint("playlists", __name__, url_prefix="/playlists")

from . import routes  # noqa: E402,F401
