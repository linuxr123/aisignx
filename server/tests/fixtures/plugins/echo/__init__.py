"""Echo test plugin: fetch() returns the message, a counter and one PNG asset.

Tests replace FETCH_MODE / module functions to simulate failures and slow upstreams.
"""
import io
import time

from aisignx.plugins.fetch import FetchError
from aisignx.plugins.schema import SchemaError
from aisignx.plugins.service import FetchResult

CALLS = []
FETCH_MODE = {"mode": "ok", "sleep": 0.0}


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (10, 200, 30)).save(buf, "PNG")
    return buf.getvalue()


def normalize(config, ctx):
    if config.get("message") == "forbidden":
        raise SchemaError("This message is not allowed.", "message")
    config["message"] = config["message"].strip()
    return config


def fetch(config, ctx):
    CALLS.append(dict(config))
    if FETCH_MODE["sleep"]:
        time.sleep(FETCH_MODE["sleep"])
    if FETCH_MODE["mode"] == "fail":
        raise FetchError("http_503")
    if FETCH_MODE["mode"] == "crash":
        raise RuntimeError("secret upstream text at internal.host")
    token = ctx.secret("token")
    return FetchResult(
        data={"message": config["message"], "n": len(CALLS), "has_token": token is not None},
        assets=[ctx.asset(_png(), "image/png")])


def playable(media, config, ctx):
    return config.get("message") != "unplayable"
