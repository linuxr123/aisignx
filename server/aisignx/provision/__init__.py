"""Android provisioning: a QR code that sets up a factory-reset tablet as a managed kiosk."""
from flask import Blueprint

bp = Blueprint("provision", __name__, url_prefix="/provision")
# A short public address for the install page, so it can be typed on a TV remote: /get/<code>.
get_bp = Blueprint("install_link", __name__, url_prefix="/get")

from . import routes, windows  # noqa: E402,F401
