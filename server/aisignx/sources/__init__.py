"""Image sources: folders, shares and cloud albums whose images are kept in the media library (admin page: Plugins > Image sources)."""
from flask import Blueprint

bp = Blueprint("sources", __name__, url_prefix="/sources")

from . import routes  # noqa: E402,F401
