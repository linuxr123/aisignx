"""Failed sign-in rate limiting, kept in memory.

Two sliding windows guard the login form: one per client IP address and one per user
name (lower-cased). Only failures count; a successful sign-in clears its user bucket.
Limits come from config: LOGIN_MAX_PER_IP, LOGIN_MAX_PER_USER, LOGIN_WINDOW_SECONDS
(a limit of 0 or less turns that bucket off). IPv6 clients are counted per /64 network,
since one host usually owns a whole /64.

State lives in the running process only. AiSignX runs as one process (waitress), so this
is enough; a restart clears every bucket.
"""
import ipaddress
import math
import threading
import time
from collections import OrderedDict, deque

from flask import current_app

EXT_KEY = "aisignx_login_limiters"
LOG_INTERVAL_S = 60.0
_SWEEP_INTERVAL_S = 60.0


class _Bucket:
    __slots__ = ("hits", "last_log")

    def __init__(self):
        self.hits = deque()
        self.last_log = None


class FailureLimiter:
    """Counts failures per key in a sliding window of `window_s` seconds.

    A key is blocked once it has `limit` failures inside the window. At most `max_keys`
    keys are tracked. Past that, idle keys go first, then the key with the fewest
    failures in the window (least recently used among equals), so flooding the table
    with fresh keys cannot reset a key that is close to its limit.
    """

    def __init__(self, limit: int, window_s: float, max_keys: int = 10000):
        self.limit = int(limit)
        self.window_s = float(window_s)
        self.max_keys = max(1, int(max_keys))
        self._buckets: "OrderedDict[str, _Bucket]" = OrderedDict()
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    @property
    def enabled(self) -> bool:
        return self.limit > 0 and self.window_s > 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._buckets)

    # --- internals (call with the lock held) ---------------------------------------------

    def _prune(self, bucket: _Bucket, now: float) -> None:
        cutoff = now - self.window_s
        while bucket.hits and bucket.hits[0] <= cutoff:
            bucket.hits.popleft()

    def _idle(self, bucket: _Bucket, now: float) -> bool:
        self._prune(bucket, now)
        recent_log = bucket.last_log is not None and now - bucket.last_log < LOG_INTERVAL_S
        return not bucket.hits and not recent_log

    def _sweep(self, now: float) -> None:
        """Drop idle keys now and then, so the table does not grow forever."""
        if now - self._last_sweep < _SWEEP_INTERVAL_S:
            return
        self._last_sweep = now
        for key in [k for k, b in self._buckets.items() if self._idle(b, now)]:
            del self._buckets[key]

    def _evict(self, keep: str, now: float) -> None:
        """Shrink the table to max_keys without dropping `keep` (the key being added)."""
        for key in [k for k, b in self._buckets.items() if k != keep and self._idle(b, now)]:
            del self._buckets[key]
        while len(self._buckets) > self.max_keys:
            # min() returns the first of equals, and the dict is in least-recently-used order.
            victim = min((k for k in self._buckets if k != keep),
                         key=lambda k: len(self._buckets[k].hits))
            del self._buckets[victim]

    def _get(self, key: str, now: float, create: bool) -> "_Bucket | None":
        self._sweep(now)
        bucket = self._buckets.get(key)
        if bucket is None:
            if not create:
                return None
            bucket = self._buckets[key] = _Bucket()
            if len(self._buckets) > self.max_keys:
                self._evict(key, now)
        else:
            self._buckets.move_to_end(key)
        self._prune(bucket, now)
        return bucket

    # --- public API ----------------------------------------------------------------------

    def retry_after(self, key: str) -> "int | None":
        """Seconds until `key` may try again, or None when it is not blocked."""
        if not self.enabled:
            return None
        now = time.monotonic()
        with self._lock:
            bucket = self._get(key, now, create=False)
            if bucket is None or len(bucket.hits) < self.limit:
                return None
            # The block lifts when enough old failures leave the window.
            unblock_at = bucket.hits[len(bucket.hits) - self.limit] + self.window_s
            return max(1, math.ceil(unblock_at - now))

    def fail(self, key: str) -> bool:
        """Count one failure. True when this failure reached the limit."""
        if not self.enabled:
            return False
        now = time.monotonic()
        with self._lock:
            bucket = self._get(key, now, create=True)
            bucket.hits.append(now)
            return len(bucket.hits) >= self.limit

    def reset(self, key: str) -> None:
        with self._lock:
            self._buckets.pop(key, None)

    def should_log(self, key: str) -> bool:
        """True at most once per key per minute, to keep the audit log readable."""
        now = time.monotonic()
        with self._lock:
            bucket = self._get(key, now, create=True)
            if bucket.last_log is not None and now - bucket.last_log < LOG_INTERVAL_S:
                return False
            bucket.last_log = now
            return True


def limiters(app=None) -> dict:
    """The app's {"ip": FailureLimiter, "user": FailureLimiter}, created on first use."""
    app = app or current_app._get_current_object()
    found = app.extensions.get(EXT_KEY)
    if found is None:
        window = app.config.get("LOGIN_WINDOW_SECONDS", 15 * 60)
        found = app.extensions.setdefault(EXT_KEY, {
            "ip": FailureLimiter(app.config.get("LOGIN_MAX_PER_IP", 20), window),
            "user": FailureLimiter(app.config.get("LOGIN_MAX_PER_USER", 5), window),
        })
    return found


def user_key(username: "str | None") -> str:
    return (username or "").strip().lower()


def ip_key(ip: "str | None") -> str:
    """The bucket for a client address: the address itself, or its /64 for IPv6."""
    if not ip:
        return "unknown"
    try:
        addr = ipaddress.ip_address(ip.split("%", 1)[0])
    except ValueError:
        return ip
    if addr.version == 6:
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


def login_blocked(ip: "str | None", username: "str | None") -> "tuple[int, str] | None":
    """(retry_after_seconds, "ip" | "user") when this sign-in must be refused, else None."""
    lims = limiters()
    waits = []
    ip_wait = lims["ip"].retry_after(ip_key(ip))
    if ip_wait:
        waits.append((ip_wait, "ip"))
    user_wait = lims["user"].retry_after(user_key(username))
    if user_wait:
        waits.append((user_wait, "user"))
    return max(waits) if waits else None


def login_failed(ip: "str | None", username: "str | None") -> None:
    lims = limiters()
    lims["ip"].fail(ip_key(ip))
    lims["user"].fail(user_key(username))


def login_succeeded(username: "str | None") -> None:
    """Clear the user's bucket. The IP bucket is kept, so one good account cannot be used
    to reset an IP that is guessing other accounts."""
    limiters()["user"].reset(user_key(username))


def api_blocked(ip: "str | None") -> "int | None":
    """Seconds to wait before this IP may try API authentication again, or None."""
    return limiters()["ip"].retry_after(ip_key(ip))


def api_failed(ip: "str | None") -> None:
    """Count a failed API authentication against the IP bucket."""
    limiters()["ip"].fail(ip_key(ip))
