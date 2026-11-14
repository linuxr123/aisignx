"""Proof of play: reports of what played where (read-only for every signed-in user)."""
from flask import Blueprint

bp = Blueprint("pop", __name__, url_prefix="/reports")

from . import routes  # noqa: E402,F401
