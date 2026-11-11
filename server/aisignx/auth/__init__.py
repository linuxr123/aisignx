"""Login, logout and the user's own profile."""
from flask import Blueprint

bp = Blueprint("auth", __name__)

from . import routes  # noqa: E402,F401
