"""Backups: create, download, upload and restore archives (admin)."""
from flask import Blueprint

bp = Blueprint("backup", __name__, url_prefix="/admin/backups")

from . import routes  # noqa: E402,F401
