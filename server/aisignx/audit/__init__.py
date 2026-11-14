"""Audit log: who changed what. record() lives in service.py; the viewer in routes.py."""
from flask import Blueprint

bp = Blueprint("audit", __name__, url_prefix="/admin/audit")

from . import routes  # noqa: E402,F401
