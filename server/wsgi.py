"""WSGI entry point.

Production:  flask --app wsgi serve [--host 0.0.0.0] [--port 5000] [--threads N]
             (waitress, with threads sized for the screens' live-update streams)
Home/dev:    flask --app wsgi run
Run a single process: live push to screens only reaches streams in the same process.
"""
from aisignx import create_app

app = create_app()
