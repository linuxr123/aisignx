"""GET /player/: the standalone browser player page (no login; the display cookie is
the credential, and the API decides whether this screen is paired).

The page lists the files of every *installed* plugin, enabled or not, so enabling a plugin
changes only the manifest and never this page (the kiosk shells cache the page and every
file it references):
    frame plugins   one <link> per runner file inside an inert <template id="plugin-files">
                    (plus the SDK), without a query string: the URLs the runner frames load;
    native plugins  real <script>/<link rel=stylesheet> tags for player_script/player_style.
"""
from flask import current_app, make_response, render_template

from . import current_build, page_bp

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "media-src 'self'; connect-src 'self'; frame-src http: https:; base-uri 'none'; "
    "form-action 'none'; frame-ancestors 'none'"
)
PLUGINS_EXT = "aisignx_plugins"  # plugins/registry.py EXT_KEY
SDK_FILES = ("player/plugins/sdk.js", "player/plugins/sdk.css")


def plugin_page_files() -> dict:
    """Static paths (under static/) the player page lists, from the plugin registry:
    {"files": runner files for the template, "scripts": native scripts, "styles": native styles}."""
    registry = current_app.extensions.get(PLUGINS_EXT)
    plugins = sorted(registry.all(), key=lambda p: p.key) if registry is not None else []
    files, scripts, styles = list(SDK_FILES), [], []
    for plugin in plugins:
        base = f"player/plugins/{plugin.key}/"
        tagged = (plugin.player_script, plugin.player_style) if plugin.render == "native" else ()
        files.extend(base + name for name in plugin.files if name not in tagged)
        if plugin.render == "native":
            if plugin.player_script:
                scripts.append(base + plugin.player_script)
            if plugin.player_style:
                styles.append(base + plugin.player_style)
    return {"files": files, "scripts": scripts, "styles": styles}


@page_bp.get("/")
def index():
    html = render_template("player/index.html", build=current_build(), plugins=plugin_page_files())
    resp = make_response(html)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Content-Security-Policy"] = CSP
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
