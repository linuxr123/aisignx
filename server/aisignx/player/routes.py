"""GET /player/: the standalone browser player page (no login; the display cookie is
the credential, and the API decides whether this screen is paired)."""
from flask import make_response, render_template

from . import current_build, page_bp

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self'; connect-src 'self'; frame-src http: https:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)


@page_bp.get("/")
def index():
    resp = make_response(render_template("player/index.html", build=current_build()))
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
