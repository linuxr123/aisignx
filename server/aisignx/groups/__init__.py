"""Display groups: flat sets of screens that schedules and emergencies can target."""
from flask import Blueprint

bp = Blueprint("groups", __name__, url_prefix="/groups")

from . import routes  # noqa: E402,F401
