"""In-process publish/subscribe for live updates to screens (Server-Sent Events).

One broker per app, in app.extensions. Each open /events stream holds one Subscriber.
Events are hints ("manifest changed"), so a full queue simply drops the new one:
the client already has a hint waiting and will fetch the latest manifest anyway.
Emergency events are published with force=True and always get through.

Only streams in the same process are reached. With several processes, screens still
converge through the heartbeat's manifest_version.
"""
import itertools
import queue
import threading

from flask import current_app

BROKER_KEY = "aisignx_broker"
QUEUE_SIZE = 16
_sequence = itertools.count()  # next() on itertools.count is atomic under the GIL


class Busy(Exception):
    """Too many open streams."""


class Subscriber:
    __slots__ = ("display_id", "queue", "created")

    def __init__(self, display_id: int):
        self.display_id = display_id
        self.queue: queue.Queue = queue.Queue(maxsize=QUEUE_SIZE)
        # Strictly increasing, so "oldest" is never a tie (time.monotonic() ticks only
        # every ~15.6 ms on Windows before Python 3.13).
        self.created = next(_sequence)

    def __repr__(self) -> str:
        return f"<Subscriber display={self.display_id}>"


def _force_put(sub: Subscriber, item) -> None:
    """Deliver item even if the queue is full, dropping older entries to make room."""
    while True:
        try:
            sub.queue.put_nowait(item)
            return
        except queue.Full:
            try:
                sub.queue.get_nowait()
            except queue.Empty:
                pass


def _offer(subs, event: str, data: dict, force: bool = False) -> None:
    for sub in subs:
        if force:
            _force_put(sub, (event, data))
            continue
        try:
            sub.queue.put_nowait((event, data))
        except queue.Full:
            pass  # hints coalesce


class Broker:
    def __init__(self, max_streams: int, per_display: int = 4):
        self.max_streams = max_streams
        self.per_display = per_display
        self._lock = threading.Lock()
        self._subs: dict[int, set[Subscriber]] = {}

    def subscribe(self, display_id: int) -> Subscriber:
        """Register a stream. A display's oldest stream is replaced when it has too many.

        Raises Busy when the server-wide stream cap is reached.
        """
        with self._lock:
            subs = self._subs.setdefault(display_id, set())
            while subs and len(subs) >= self.per_display:
                oldest = min(subs, key=lambda s: s.created)
                _force_put(oldest, ("bye", {"reason": "replaced"}))
                subs.discard(oldest)
            if self._count_locked() >= self.max_streams:
                if not subs:
                    del self._subs[display_id]
                raise Busy()
            sub = Subscriber(display_id)
            subs.add(sub)
            return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        """Remove exactly this subscriber. Idempotent; never touches a newer stream."""
        with self._lock:
            subs = self._subs.get(sub.display_id)
            if subs is None:
                return
            subs.discard(sub)
            if not subs:
                del self._subs[sub.display_id]

    def publish(self, display_id: int, event: str, data: dict, force: bool = False) -> int:
        """Send an event to one display's streams. Returns how many streams it reached.

        force=True drops the oldest queued hints if needed, so the event is never lost
        (emergency events carry data, unlike hints).
        """
        with self._lock:
            targets = list(self._subs.get(display_id, ()))
        _offer(targets, event, data, force)
        return len(targets)

    def publish_all(self, event: str, data: dict) -> int:
        """Send an event to every open stream. Returns how many streams it reached."""
        with self._lock:
            targets = [sub for subs in self._subs.values() for sub in subs]
        _offer(targets, event, data)
        return len(targets)

    def close(self, display_id: int) -> int:
        """Tell a display's streams that its credential is gone, and forget them."""
        with self._lock:
            subs = self._subs.pop(display_id, set())
        for sub in subs:
            _force_put(sub, ("revoked", {}))
        return len(subs)

    def connected(self, display_id: int) -> bool:
        with self._lock:
            return bool(self._subs.get(display_id))

    def count(self) -> int:
        with self._lock:
            return self._count_locked()

    def _count_locked(self) -> int:
        return sum(len(subs) for subs in self._subs.values())


def get_broker() -> Broker | None:
    return current_app.extensions.get(BROKER_KEY)
