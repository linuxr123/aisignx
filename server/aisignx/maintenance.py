"""Maintenance mode: while a restore runs, every request except a few gets a 503.

A module flag, so it covers this process only (run one server process).
"""
import threading

from flask import Flask, Response, jsonify, request

RETRY_AFTER_S = 30
# Kiosk probes, static files and the restore progress poll keep working.
EXEMPT_ENDPOINTS = {"main.healthz", "static", "backup.status"}

_lock = threading.Lock()
_reason: str | None = None

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Maintenance · AiSignX</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="30"></head>
<body style="font-family:system-ui,sans-serif;max-width:36rem;margin:4rem auto;padding:0 1rem">
<h1 style="font-size:1.4rem">AiSignX is under maintenance</h1>
<p>A restore is in progress. This page reloads by itself; try again in a minute.</p>
</body></html>"""


def begin(reason: str) -> None:
    global _reason
    with _lock:
        _reason = reason or "maintenance"


def end() -> None:
    global _reason
    with _lock:
        _reason = None


def active() -> str | None:
    """The reason given to begin(), or None when not in maintenance."""
    with _lock:
        return _reason


def register(app: Flask) -> None:
    @app.before_request
    def maintenance_guard():
        if active() is None or request.endpoint in EXEMPT_ENDPOINTS:
            return None
        if request.path.startswith("/api/"):
            resp = jsonify(error="maintenance",
                           message="AiSignX is under maintenance. Try again shortly.")
        else:
            resp = Response(_PAGE, mimetype="text/html")
        resp.status_code = 503
        resp.headers["Retry-After"] = str(RETRY_AFTER_S)
        resp.headers["Cache-Control"] = "no-store"
        return resp
