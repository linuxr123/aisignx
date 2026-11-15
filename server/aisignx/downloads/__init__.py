"""Downloads page: installers for the player apps and the PowerPoint add-in."""
from flask import Blueprint

bp = Blueprint("downloads", __name__, url_prefix="/downloads")

from . import routes  # noqa: E402,F401
