"""System health: dependency checks, self-test and the log viewer (admin).

`Check`, `run_checks` and `register` (the `flask check` command) live in checks.py and
are re-exported here, so `from aisignx.system import run_checks` keeps working.
"""
from flask import Blueprint

bp = Blueprint("system", __name__, url_prefix="/admin/system")

from .checks import Check, register, run_checks  # noqa: E402,F401
from . import routes  # noqa: E402,F401
