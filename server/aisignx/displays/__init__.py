"""Displays: pair screens, assign playlists, see who is online."""
from flask import Blueprint

bp = Blueprint("displays", __name__, url_prefix="/displays")

from . import agent_admin, routes  # noqa: E402,F401
