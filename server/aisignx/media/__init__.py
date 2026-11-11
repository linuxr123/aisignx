"""Media library: upload images and videos, add web pages."""
from flask import Blueprint

bp = Blueprint("media", __name__, url_prefix="/media")

from . import routes  # noqa: E402,F401
