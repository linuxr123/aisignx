"""Plugins: bundled content types (clock, weather, RSS, ...) that become library items.

    registry.py  plugin.json discovery and validation (app.extensions, no globals)
    schema.py    config field types and server-side validation
    service.py   plugin items, settings, secrets, the hook context, manifest fields
    routes.py    admin pages (/plugins)
    data_api.py  player data and asset endpoints (/api/player/v1/plugins/...)
    fetch.py     the SSRF-safe HTTP client
    cache.py     file cache with stale-while-revalidate
    worker.py    the background executor for fetches
    geo.py       geocoding for location fields

See docs/plugins.md for the plugin author contract.
"""
from flask import Blueprint, Flask

bp = Blueprint("plugins", __name__, url_prefix="/plugins")


def init_app(app: Flask) -> None:
    """Load the registry (a bad plugin.json fails here), the cache and the worker."""
    from . import cache, registry, worker

    registry.init_app(app)
    cache.init_app(app)
    worker.init_app(app)


from . import routes  # noqa: E402,F401
