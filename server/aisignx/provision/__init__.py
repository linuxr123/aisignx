"""Android provisioning: a QR code that sets up a factory-reset tablet as a managed kiosk."""
from flask import Blueprint

bp = Blueprint("provision", __name__, url_prefix="/provision")

from . import routes  # noqa: E402,F401
