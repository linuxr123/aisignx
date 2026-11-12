"""Screens: the browser player page and the JSON/SSE API that players talk to.

Two blueprints:
    player      /player/          the standalone browser player page
    player_api  /api/player/v1    pairing, manifest, heartbeat, media and live events
"""
import hashlib
from pathlib import Path

from flask import Blueprint, Flask, current_app

page_bp = Blueprint("player", __name__, url_prefix="/player")
api_bp = Blueprint("player_api", __name__, url_prefix="/api/player/v1")

_BUILD_KEY = "aisignx_player_build"


def init_app(app: Flask) -> None:
    """Create the broker, hook the change listeners and fingerprint the player files."""
    from . import events
    from .broker import BROKER_KEY, Broker

    app.extensions[BROKER_KEY] = Broker(int(app.config["PLAYER_MAX_STREAMS"]))
    events.register_listeners()
    app.extensions[_BUILD_KEY] = _compute_build(app)


def current_build() -> str:
    """The player build id: changes whenever a file under static/player/ changes."""
    return current_app.extensions[_BUILD_KEY]


def _compute_build(app: Flask) -> str:
    from .. import __version__

    digest = hashlib.sha256()
    folder = Path(app.static_folder or "") / "player"
    if folder.is_dir():  # tolerate a missing folder
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
            digest.update(path.read_bytes())
    return f"{__version__}-{digest.hexdigest()[:8]}"


from . import api, routes, stream  # noqa: E402,F401
