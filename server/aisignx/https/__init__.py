"""HTTPS for the web page: certificates (self-signed or bought), the nginx configuration, and the admin page for them."""
from flask import Blueprint

bp = Blueprint("https", __name__, url_prefix="/admin/https")

from . import routes  # noqa: E402,F401
