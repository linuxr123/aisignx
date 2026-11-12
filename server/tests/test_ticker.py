"""The schedule ticker (player/ticker.py) and the time zone change push."""
import queue
import threading
from datetime import datetime, time, timezone

import pytest

from aisignx.extensions import db
from aisignx.models import Display, Media, Playlist, PlaylistItem, Schedule, Setting
from aisignx.player import ticker
from aisignx.player.broker import get_broker

UTC = timezone.utc


def z(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def _drain(sub) -> list:
    events = []
    while True:
        try:
            events.append(sub.queue.get_nowait())
        except queue.Empty:
            return events


@pytest.fixture
def screens(app):
    """Two paired displays; a 08:00-09:00 UTC schedule targets only the first.

    Yields (lobby_id, hall_id, {display_id: broker subscriber}).
    """
    with app.app_context():
        playlist = Playlist(name="Breakfast")
        playlist.items.append(PlaylistItem(position=0, media=Media(
            name="Menu", media_type="web", url="https://example.com", default_duration=10)))
        lobby = Display(name="Lobby", token_hash="a" * 64)
        hall = Display(name="Hall", token_hash="b" * 64)
        db.session.add_all([playlist, lobby, hall])
        db.session.flush()
        db.session.add(Schedule(name="Mornings", playlist=playlist, scope="display",
                                display_id=lobby.id, start_time=time(8), end_time=time(9)))
        db.session.commit()
        broker = get_broker()
        subs = {lobby.id: broker.subscribe(lobby.id), hall.id: broker.subscribe(hall.id)}
        ids = (lobby.id, hall.id)
    yield (*ids, subs)
    for sub in subs.values():
        broker.unsubscribe(sub)


def test_first_tick_only_seeds(app, screens):
    lobby, hall, subs = screens
    last = {}
    assert ticker.tick(app, last, z(2026, 11, 12, 8, 30)) == []
    assert set(last) == {lobby, hall}
    assert all(_drain(sub) == [] for sub in subs.values())


def test_boundary_notifies_only_affected_display(app, screens):
    lobby, hall, subs = screens
    last = {}
    ticker.tick(app, last, z(2026, 11, 12, 7, 59))
    assert ticker.tick(app, last, z(2026, 11, 12, 8, 0, 0, 500_000)) == [lobby]
    assert _drain(subs[lobby]) == [("manifest", {})]
    assert _drain(subs[hall]) == []

    # Unchanged fingerprint: nothing.
    assert ticker.tick(app, last, z(2026, 11, 12, 8, 1)) == []
    assert all(_drain(sub) == [] for sub in subs.values())

    # The end boundary flips it back.
    assert ticker.tick(app, last, z(2026, 11, 12, 9, 0, 0, 500_000)) == [lobby]
    assert _drain(subs[lobby]) == [("manifest", {})]


def test_unpaired_display_is_dropped(app, screens):
    lobby, hall, _ = screens
    last = {}
    ticker.tick(app, last, z(2026, 11, 12, 7))
    with app.app_context():
        db.session.get(Display, hall).token_hash = None
        db.session.commit()
    ticker.tick(app, last, z(2026, 11, 12, 7, 1))
    assert set(last) == {lobby}


def test_tick_never_writes(app, screens):
    """Read-only: no INSERT/UPDATE/DELETE is executed, and an unchanged pass publishes nothing."""
    from sqlalchemy import event

    _, _, subs = screens
    writes = []

    def record(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith(("SELECT", "PRAGMA")):
            writes.append(statement)

    with app.app_context():
        engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        last = {}
        ticker.tick(app, last, z(2026, 11, 12, 7))
        for sub in subs.values():
            _drain(sub)
        assert ticker.tick(app, last, z(2026, 11, 12, 7, 1)) == []
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert writes == []
    assert all(_drain(sub) == [] for sub in subs.values())


def test_timezone_change_publishes_manifest(app, admin_client, screens):
    lobby, _, subs = screens
    form = {"org_name": "Org", "timezone": "Europe/Berlin", "default_duration": "10"}
    _drain(subs[lobby])
    assert admin_client.post("/settings/", data=form).status_code == 302
    with app.app_context():
        assert Setting.get("timezone") == "Europe/Berlin"
    assert ("manifest", {}) in _drain(subs[lobby])

    # Saving the same zone again publishes nothing.
    assert admin_client.post("/settings/", data=form).status_code == 302
    assert _drain(subs[lobby]) == []


def test_ensure_started_is_a_noop_when_disabled(app):
    assert app.config["PLAYER_SCHEDULE_TICKER"] is False
    ticker.ensure_started(app)
    assert ticker._KEY not in app.extensions


def test_ensure_started_is_idempotent_and_restarts(app, monkeypatch):
    stop = threading.Event()
    monkeypatch.setattr(ticker, "_run", lambda _app: stop.wait(5))
    app.config["PLAYER_SCHEDULE_TICKER"] = True
    try:
        ticker.ensure_started(app)
        first = app.extensions[ticker._KEY]
        assert first.is_alive() and first.daemon
        ticker.ensure_started(app)
        assert app.extensions[ticker._KEY] is first
    finally:
        stop.set()
    first.join(5)
    assert not first.is_alive()

    stop.clear()
    try:
        ticker.ensure_started(app)  # a dead thread is replaced
        second = app.extensions[ticker._KEY]
        assert second is not first and second.is_alive()
    finally:
        stop.set()
        second.join(5)


def test_api_starts_the_ticker_only_after_setup(app, client, monkeypatch):
    from aisignx.player import api

    from .conftest import make_user

    started = []
    monkeypatch.setattr(api, "ensure_started", started.append)
    assert client.get("/api/player/v1/manifest").status_code == 503  # setup_required
    assert started == []
    make_user(app, "admin", "admin")
    assert client.get("/api/player/v1/manifest").status_code == 401
    assert started == [app]
