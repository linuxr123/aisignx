"""Live push: the in-process broker, the commit hook, and the /events SSE stream.

SSE tests use tiny keepalive/max-age values and read the response unbuffered with
hard limits on chunks and wall time, so a broken stream fails instead of hanging.
"""
import json
import queue
import time

import pytest

from aisignx.extensions import db
from aisignx.media import service as media_service
from aisignx.models import Display, Media, Playlist, PlaylistItem
from aisignx.player import current_build
from aisignx.player.broker import QUEUE_SIZE, Broker, Busy, get_broker

from .conftest import DISPLAY_TOKEN, bearer

API = "/api/player/v1"
KEEPALIVE = 0.05
MAX_AGE = 0.3


def _drain(sub) -> list:
    events = []
    while True:
        try:
            events.append(sub.queue.get_nowait())
        except queue.Empty:
            return events


# --- broker -----------------------------------------------------------------------------

def test_broker_publish_reaches_all_subscribers():
    broker = Broker(max_streams=10)
    a, b = broker.subscribe(1), broker.subscribe(1)
    other = broker.subscribe(2)
    assert broker.publish(1, "manifest", {}) == 2
    assert _drain(a) == _drain(b) == [("manifest", {})]
    assert _drain(other) == []
    assert broker.publish(3, "manifest", {}) == 0
    assert broker.publish_all("manifest", {"x": 1}) == 3
    assert [len(_drain(s)) for s in (a, b, other)] == [1, 1, 1]
    assert broker.count() == 3
    assert broker.connected(1) and broker.connected(2) and not broker.connected(3)


def test_broker_unsubscribe_is_exact_and_idempotent():
    broker = Broker(max_streams=10)
    stale = broker.subscribe(1)
    fresh = broker.subscribe(1)  # a reconnect
    broker.unsubscribe(stale)
    broker.unsubscribe(stale)
    assert broker.count() == 1 and broker.connected(1)
    assert broker.publish(1, "manifest", {}) == 1
    assert _drain(fresh) == [("manifest", {})]
    broker.unsubscribe(fresh)
    broker.unsubscribe(fresh)
    assert broker.count() == 0 and not broker.connected(1)


def test_broker_full_queue_does_not_raise():
    broker = Broker(max_streams=10)
    sub = broker.subscribe(1)
    for _ in range(QUEUE_SIZE + 10):
        assert broker.publish(1, "manifest", {}) == 1
    assert sub.queue.qsize() == QUEUE_SIZE


def test_broker_busy_at_max_streams():
    broker = Broker(max_streams=2)
    broker.subscribe(1)
    broker.subscribe(2)
    with pytest.raises(Busy):
        broker.subscribe(3)
    assert broker.count() == 2 and not broker.connected(3)
    with pytest.raises(Busy):
        Broker(max_streams=0).subscribe(1)


def test_broker_fifth_subscriber_replaces_oldest():
    broker = Broker(max_streams=10)
    subs = []
    for _ in range(4):
        subs.append(broker.subscribe(7))
        time.sleep(0.002)  # distinct monotonic creation times
    newest = broker.subscribe(7)
    assert broker.count() == 4
    assert _drain(subs[0]) == [("bye", {"reason": "replaced"})]
    assert all(_drain(s) == [] for s in subs[1:])
    assert broker.publish(7, "manifest", {}) == 4
    assert _drain(subs[0]) == []  # removed: gets nothing more
    assert _drain(newest) == [("manifest", {})]


def test_broker_replacement_reaches_full_queue():
    broker = Broker(max_streams=10, per_display=1)
    old = broker.subscribe(1)
    for _ in range(QUEUE_SIZE):
        broker.publish(1, "manifest", {})
    broker.subscribe(1)
    assert _drain(old)[-1] == ("bye", {"reason": "replaced"})


def test_broker_close_delivers_revoked_even_when_full():
    broker = Broker(max_streams=10)
    a, b = broker.subscribe(1), broker.subscribe(1)
    other = broker.subscribe(2)
    for _ in range(QUEUE_SIZE):
        broker.publish(1, "manifest", {})
    assert broker.close(1) == 2
    assert _drain(a)[-1] == ("revoked", {})
    assert _drain(b)[-1] == ("revoked", {})
    assert not broker.connected(1) and broker.connected(2)
    assert _drain(other) == []
    assert broker.close(1) == 0


# --- commit hook ------------------------------------------------------------------------

@pytest.fixture
def listener(app):
    """A broker subscriber that is not tied to any display stream."""
    with app.app_context():
        broker = get_broker()
        sub = broker.subscribe(999_999)
    yield sub
    broker.unsubscribe(sub)


@pytest.fixture
def item_id(app, configured):
    with app.app_context():
        media = Media(name="Menu", media_type="web", url="https://example.com",
                      default_duration=20)
        playlist = Playlist(name="P")
        playlist.items.append(PlaylistItem(media=media, position=0))
        db.session.add(playlist)
        db.session.commit()
        return playlist.items[0].id


def test_commit_of_watched_change_publishes_once(app, item_id, listener):
    _drain(listener)
    with app.app_context():
        item = db.session.get(PlaylistItem, item_id)
        item.duration = 33
        item.fit_mode = "fill"
        db.session.flush()
        db.session.get(PlaylistItem, item_id).transition = "zoom"
        db.session.commit()  # two flushes, one commit
    assert _drain(listener) == [("manifest", {})]


def test_commit_without_watched_change_publishes_nothing(app, configured, listener):
    from aisignx.models import Setting
    _drain(listener)
    with app.app_context():
        Setting.set("org_name", "Elsewhere")
        db.session.commit()
    assert _drain(listener) == []


def test_rollback_publishes_nothing(app, item_id, listener):
    _drain(listener)
    with app.app_context():
        db.session.get(PlaylistItem, item_id).duration = 44
        db.session.flush()
        db.session.rollback()
        db.session.commit()  # the flag must not survive the rollback
    assert _drain(listener) == []
    with app.app_context():
        assert db.session.get(PlaylistItem, item_id).duration is None


def test_heartbeat_publishes_nothing(app, configured, client, paired_display, listener):
    _drain(listener)
    resp = client.post(f"{API}/heartbeat", headers=bearer(DISPLAY_TOKEN),
                       json={"client_id": "abc", "screen_w": 800, "screen_h": 600})
    assert resp.status_code == 200
    assert client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).status_code == 200
    assert _drain(listener) == []


def test_media_delete_service_publishes(app, item_id, listener):
    _drain(listener)
    with app.test_request_context():
        media_service.delete_media(db.session.get(PlaylistItem, item_id).media)
    assert _drain(listener) == [("manifest", {})]


def test_display_change_publishes(app, configured, paired_display, listener):
    _drain(listener)
    with app.app_context():
        db.session.get(Display, paired_display[0]).name = "Front desk"
        db.session.commit()
    assert _drain(listener) == [("manifest", {})]


# --- SSE --------------------------------------------------------------------------------

@pytest.fixture
def sse_app(app, configured, paired_display):
    app.config.update(PLAYER_SSE_KEEPALIVE_SECONDS=KEEPALIVE,
                      PLAYER_SSE_MAX_AGE_SECONDS=MAX_AGE)
    return app


def _parse(chunk: str):
    """Split SSE text into ('comment', text) / ('retry', ms) / (event, data) tuples."""
    out = []
    for block in chunk.split("\n\n"):
        if not block:
            continue
        event = data = None
        for line in block.split("\n"):
            if line.startswith(":"):
                out.append(("comment", line[1:].strip()))
            elif line.startswith("retry: "):
                out.append(("retry", int(line[7:])))
            elif line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if event is not None:
            out.append((event, data))
    return out


def _read(resp, max_chunks=200, max_seconds=5.0, until=None):
    """Read an unbuffered streaming response with hard limits. Returns parsed frames."""
    frames, raw = [], []
    deadline = time.monotonic() + max_seconds
    iterator = iter(resp.response)
    for _ in range(max_chunks):
        if time.monotonic() > deadline:
            pytest.fail("SSE stream did not end in time")
        try:
            chunk = next(iterator)
        except StopIteration:
            break
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk
        raw.append(text)
        frames.extend(_parse(text))
        if until and until(frames):
            break
    else:
        pytest.fail("SSE stream produced too many chunks")
    return frames, "".join(raw)


def _open(client, token=DISPLAY_TOKEN):
    return client.get(f"{API}/events", headers=bearer(token), buffered=False)


def test_sse_headers(sse_app, client):
    resp = _open(client)
    try:
        assert resp.status_code == 200
        assert resp.mimetype == "text/event-stream"
        assert resp.headers["Cache-Control"] == "no-cache"
        assert resp.headers["X-Accel-Buffering"] == "no"
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert "Connection" not in resp.headers
        assert "Content-Encoding" not in resp.headers
        assert "Content-Length" not in resp.headers
    finally:
        resp.close()
    with sse_app.app_context():
        assert get_broker().count() == 0


def test_sse_stream_sequence(sse_app, client, paired_display):
    display_id = paired_display[0]
    with sse_app.app_context():
        broker = get_broker()
        build = current_build()
    resp = _open(client)
    try:
        assert resp.status_code == 200
        with sse_app.app_context():
            assert broker.connected(display_id)
            # The stream holds no database connection while it is open.
            assert db.engine.pool.checkedout() == 0
        assert broker.publish(display_id, "manifest", {}) == 1

        frames, raw = _read(resp)
    finally:
        resp.close()

    assert raw.startswith(":" + " " * 2048 + "\n")
    assert frames[0] == ("comment", "")
    assert frames[1] == ("retry", 5000)
    event, hello = frames[2]
    assert event == "hello"
    assert set(hello) == {"build", "manifest_version", "server_time_ms", "heartbeat_s",
                          "display"}
    assert hello["build"] == build
    assert hello["display"] == {"id": display_id, "name": "Lobby"}
    assert hello["heartbeat_s"] == 30
    version = client.get(f"{API}/manifest", headers=bearer(DISPLAY_TOKEN)).get_json()["version"]
    assert hello["manifest_version"] == version
    assert frames[3] == ("manifest", {})
    rest = frames[4:]
    assert ("ka", {}) in rest
    assert rest[-1] == ("bye", {"reason": "max_age"})
    assert all(f == ("ka", {}) for f in rest[:-1])

    with sse_app.app_context():
        assert broker.count() == 0
        display = db.session.get(Display, display_id)
        assert display.last_seen_at is not None
        assert display.last_ip == "127.0.0.1"


def test_sse_close_before_iteration_unsubscribes(sse_app, client):
    resp = _open(client)
    with sse_app.app_context():
        assert get_broker().count() == 1
    resp.close()  # the generator never started; call_on_close must still clean up
    with sse_app.app_context():
        assert get_broker().count() == 0


def test_sse_receives_commit_hook_hint(sse_app, client, paired_display):
    resp = _open(client)
    try:
        with sse_app.app_context():
            db.session.get(Display, paired_display[0]).name = "Front desk"
            db.session.commit()
        frames, _ = _read(resp, until=lambda f: ("manifest", {}) in f)
    finally:
        resp.close()
    assert [f for f in frames if f[0] == "manifest"] == [("manifest", {})]


def test_sse_unpair_revokes_and_ends(sse_app, client, paired_display):
    display_id = paired_display[0]
    sse_app.config["PLAYER_SSE_MAX_AGE_SECONDS"] = 30  # only revoke can end it in time
    resp = _open(client)
    try:
        with sse_app.app_context():
            display = db.session.get(Display, display_id)
            display.token_hash = None
            display.paired_at = None
            db.session.commit()
            assert get_broker().close(display_id) == 1
        frames, _ = _read(resp, max_seconds=5.0)
    finally:
        resp.close()
    events = [f[0] for f in frames if f[0] not in ("comment", "retry")]
    assert events[-1] == "revoked"
    assert "bye" not in events
    with sse_app.app_context():
        assert get_broker().count() == 0
    resp = _open(client)
    assert resp.status_code == 401
    resp.close()


def test_sse_replaced_stream_gets_bye(sse_app, client):
    sse_app.config["PLAYER_SSE_MAX_AGE_SECONDS"] = 30
    first = _open(client)
    others = []
    try:
        for _ in range(4):
            time.sleep(0.002)
            others.append(_open(client))
        frames, _ = _read(first, max_seconds=5.0)
        assert frames[-1] == ("bye", {"reason": "replaced"})
        with sse_app.app_context():
            assert get_broker().count() == 4
    finally:
        first.close()
        for resp in others:
            resp.close()
    with sse_app.app_context():
        assert get_broker().count() == 0


def test_sse_busy(sse_app, client, paired_display):
    with sse_app.app_context():
        get_broker().max_streams = 0  # same as PLAYER_MAX_STREAMS=0 at startup
    resp = _open(client)
    assert resp.status_code == 503
    assert resp.get_json()["error"] == "busy"
    assert resp.headers["Retry-After"] == "30"
    resp.close()
    with sse_app.app_context():
        assert get_broker().count() == 0
        # Nothing was written for a refused stream.
        assert db.session.get(Display, paired_display[0]).last_seen_at is None


def test_max_streams_config_sizes_broker(tmp_path, monkeypatch):
    from .conftest import make_csrf_app
    app = make_csrf_app(tmp_path, monkeypatch, PLAYER_MAX_STREAMS=0)
    with app.app_context():
        assert get_broker().max_streams == 0
        db.engine.dispose()
