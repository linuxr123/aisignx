"""WSGI entry point: `flask --app wsgi run` or any WSGI server (e.g. waitress-serve wsgi:app)."""
from aisignx import create_app

app = create_app()
