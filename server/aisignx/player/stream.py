"""GET /api/player/v1/events: Server-Sent Events push channel for a paired display.

All database work happens before the response starts streaming. The generator is
deliberately NOT wrapped in stream_with_context: Flask pops the app context (and with
it the scoped session and its pooled connection) before iteration begins, so an open
stream never holds a database connection.

Each open stream pins one server thread; `flask --app wsgi serve` sizes the thread pool
from PLAYER_MAX_STREAMS.
"""
import json
import queue
import random
import time

from flask import Response, current_app, g, request
from sqlalchemy import update

from ..extensions import db
from ..models import Display
from ..timeutil import utcnow
from . import api_bp, current_build
from .api import api_error
from .auth import display_required
from .broker import Broker, Busy, Subscriber, get_broker
from .manifest import build_manifest

RETRY_MS = 5000
_PADDING = 2048  # defeats proxies that buffer the first few KB
# Keepalives are a named event rather than an SSE comment: browsers never expose comment
# lines to JavaScript, and the player's watchdog must see a frame every keepalive period.
KEEPALIVE_FRAME = "event: ka\ndata: {}\n\n"


def frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _stream(broker: Broker, sub: Subscriber, hello: dict, keepalive: float, max_age: float,
            initial: list | None = None):
    """initial: (event, data) frames sent right after hello (the live emergency)."""
    try:
        yield ":" + " " * _PADDING + f"\nretry: {RETRY_MS}\n\n"
        yield frame("hello", hello)
        for event, data in initial or ():
            yield frame(event, data)
        # Jitter the end so screens don't all reconnect at the same moment.
        end = time.monotonic() + max_age * random.uniform(1.0, 1.1)
        while time.monotonic() < end:
            timeout = max(0.01, min(keepalive, end - time.monotonic()))
            try:
                event, data = sub.queue.get(timeout=timeout)
            except queue.Empty:
                yield KEEPALIVE_FRAME
                continue
            yield frame(event, data)
            if event in ("revoked", "bye"):
                return
        yield frame("bye", {"reason": "max_age"})
    finally:
        broker.unsubscribe(sub)


@api_bp.get("/events")
@display_required
def events():
    display = g.display
    display_id, display_name = display.id, display.name
    broker = get_broker()
    # Subscribe before building the manifest, so a change committed in between still
    # reaches this stream as a "manifest" hint after hello.
    try:
        sub = broker.subscribe(display_id)
    except Busy:
        return api_error(503, "busy", "Too many live connections. Falling back to polling.",
                         retry_after=30)
    try:
        manifest = build_manifest(display)
        db.session.execute(
            update(Display).where(Display.id == display_id).values(
                last_seen_at=utcnow(), last_ip=(request.remote_addr or "")[:45] or None,
            ).execution_options(synchronize_session=False)
        )
        db.session.commit()
    except Exception:
        broker.unsubscribe(sub)
        raise

    cfg = current_app.config
    hello = {
        "build": current_build(),
        "manifest_version": manifest["version"],
        "server_time_ms": int(time.time() * 1000),
        "heartbeat_s": int(cfg["PLAYER_HEARTBEAT_SECONDS"]),
        "display": {"id": display_id, "name": display_name},
    }
    keepalive = float(cfg["PLAYER_SSE_KEEPALIVE_SECONDS"])
    max_age = float(cfg["PLAYER_SSE_MAX_AGE_SECONDS"])

    # On every (re)connect, repeat the live emergency so a screen never misses one.
    initial = [("emergency", manifest["emergency"])] if manifest.get("emergency") else []

    resp = Response(_stream(broker, sub, hello, keepalive, max_age, initial),
                    mimetype="text/event-stream")
    # A generator that never started does not run its finally block.
    resp.call_on_close(lambda: broker.unsubscribe(sub))
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    return resp
