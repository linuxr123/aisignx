"""
Rate limiting - Phase 3.

In-process token-bucket limiter. Lightweight, no external dependencies
(no Redis required). Thread-safe. Buckets are keyed by an arbitrary
string the caller chooses; the helpers below wrap common patterns:

    @limit_per_ip('login', limit=5, window_s=60)
    def login(): ...

    @limit_per_token('api', limit=600, window_s=60)
    def api_endpoint(): ...

Algorithm
---------
Classic token bucket. Each key holds (tokens, last_refill_ts). Every
request: refill at `limit / window_s` tokens per second up to `limit`,
then deduct 1; if tokens < 1, refuse. Memory is bounded by an LRU
sweep that runs every ~30 s.

Why in-process?
---------------
This server is the typical single-binary install. The single source of
truth for tenant data is also single-process (SQLite + a thread pool),
so distributing the rate limiter wouldn't buy anything. If a future
deployment needs HA, swap _Bucket._STORE for a Redis-backed map; the
public API stays identical.

What it does NOT do
-------------------
- Distributed coordination across processes
- Sliding-log accuracy (token bucket is approximate at the boundary)
- Shaping/queuing -- requests over the limit are simply refused

Audit
-----
First refusal per (key, minute) emits one `rate_limit.exceeded` audit
entry. Subsequent refusals in the same minute are silently dropped to
avoid log floods.

Settings (overridable via the System Settings UI)
-------------------------------------------------
ratelimit.enabled            (bool, default True)
ratelimit.login_per_min      (int,  default 5)   per IP
ratelimit.register_per_min   (int,  default 10)  per IP
ratelimit.browser_register_per_min (int, default 5)
ratelimit.api_per_min        (int,  default 600) per token (or per IP if no token)
"""
import threading
import time
from functools import wraps

from flask import jsonify, request, g

from logging_config import logger


# ----------------------------------------------------------------------------
# Bucket store
# ----------------------------------------------------------------------------

# Single global lock; contention is fine because each operation is O(1).
_LOCK = threading.Lock()
# {key: [tokens_remaining, last_refill_ts, last_audit_minute]}
_BUCKETS = {}
# Cap on bucket count -- prevents runaway memory if attackers cycle keys.
# At 50k buckets * ~80 bytes each ~= 4MB, well below any real budget.
_MAX_BUCKETS = 50_000
# Last LRU sweep timestamp.
_LAST_SWEEP = 0.0
_SWEEP_INTERVAL_S = 30.0


def _sweep_locked():
    """Drop buckets that haven't been touched in 5 minutes. Called only
    when _MAX_BUCKETS is approached or every _SWEEP_INTERVAL_S, whichever
    is first. Must be called with _LOCK held."""
    global _LAST_SWEEP
    now = time.time()
    cutoff = now - 300.0
    # Walk a copy of items so we can mutate the dict.
    stale = [k for k, v in _BUCKETS.items() if v[1] < cutoff]
    for k in stale:
        _BUCKETS.pop(k, None)
    _LAST_SWEEP = now
    if stale:
        logger.debug(f'rate_limit: swept {len(stale)} stale buckets, '
                     f'{len(_BUCKETS)} remain')


def _check_and_consume(key: str, limit: int, window_s: float) -> tuple[bool, float]:
    """Try to consume one token from the bucket at `key`. Returns
    (allowed, retry_after_seconds). retry_after is 0 when allowed."""
    if limit <= 0 or window_s <= 0:
        # Misconfigured -- never block. Better than locking everyone out.
        return True, 0.0
    refill_per_sec = float(limit) / float(window_s)
    now = time.time()
    with _LOCK:
        # Periodic sweep, but only when the dict is getting big enough to
        # care -- otherwise just rely on the time-based threshold.
        if (len(_BUCKETS) > _MAX_BUCKETS // 2
                and now - _LAST_SWEEP > _SWEEP_INTERVAL_S):
            _sweep_locked()
        b = _BUCKETS.get(key)
        if b is None:
            # New bucket, full.
            _BUCKETS[key] = [float(limit) - 1.0, now, 0]
            return True, 0.0
        tokens, last, last_audit_minute = b
        elapsed = now - last
        tokens = min(float(limit), tokens + elapsed * refill_per_sec)
        if tokens < 1.0:
            # Need this much time before one token is available.
            retry_after = (1.0 - tokens) / refill_per_sec
            b[0] = tokens
            b[1] = now
            return False, retry_after
        b[0] = tokens - 1.0
        b[1] = now
        return True, 0.0


def _maybe_audit(key: str):
    """Emit one audit entry per (bucket, minute). Called only on refusal."""
    minute = int(time.time() // 60)
    with _LOCK:
        b = _BUCKETS.get(key)
        if b is None:
            return False
        if b[2] == minute:
            return False
        b[2] = minute
    # Outside the lock -- audit() may itself acquire DB locks.
    try:
        from audit import audit
        from tenant_filter import bypass_tenant_filter
        with bypass_tenant_filter():    # tenant-ok: rate-limit event is system-wide
            audit('rate_limit.exceeded', target_type='rate_limit',
                  target_id=key, payload={'minute': minute})
    except Exception as e:
        # Don't let auditing failures change the limiter's behavior.
        logger.warning(f'rate_limit: audit failed for {key}: {e}')
    return True


# ----------------------------------------------------------------------------
# Settings integration
# ----------------------------------------------------------------------------

# Cache settings reads -- effective_value() hits the DB. Refresh every
# 30 s so admin tweaks land within reasonable time without thrashing.
_SETTINGS_CACHE = {'ts': 0.0, 'values': {}}
_SETTINGS_TTL_S = 30.0
_DEFAULT_LIMITS = {
    'ratelimit.enabled': True,
    'ratelimit.login_per_min': 5,
    'ratelimit.register_per_min': 10,
    'ratelimit.browser_register_per_min': 5,
    'ratelimit.api_per_min': 600,
}


def _settings():
    """Return the current settings snapshot (cached)."""
    now = time.time()
    if now - _SETTINGS_CACHE['ts'] < _SETTINGS_TTL_S and _SETTINGS_CACHE['values']:
        return _SETTINGS_CACHE['values']
    try:
        import settings as _s
        out = {}
        for k, default in _DEFAULT_LIMITS.items():
            v = _s.effective_value(k)
            out[k] = v if v is not None else default
    except Exception:
        # Boot ordering / DB hiccup -- fall back to defaults.
        out = dict(_DEFAULT_LIMITS)
    _SETTINGS_CACHE['ts'] = now
    _SETTINGS_CACHE['values'] = out
    return out


def invalidate_settings_cache():
    """Force a re-read on the next limiter call. Useful after admin
    edits if you want them to take effect immediately."""
    _SETTINGS_CACHE['ts'] = 0.0


# ----------------------------------------------------------------------------
# Identity helpers
# ----------------------------------------------------------------------------

def _client_ip():
    """Best-effort real client IP. ProxyFix should already have set
    request.remote_addr correctly behind a trusted proxy."""
    try:
        from displays import _real_client_ip
        return _real_client_ip() or 'unknown'
    except Exception:
        return request.remote_addr or 'unknown'


def _token_or_ip_key():
    """For API endpoints: prefer the token id (cheap, unique, survives
    NAT). Fall back to IP for cookie-session callers."""
    tok = getattr(g, 'api_token', None)
    if tok is not None:
        return f'tok:{tok.id}'
    return f'ip:{_client_ip()}'


# ----------------------------------------------------------------------------
# Public decorators
# ----------------------------------------------------------------------------

def _refuse(key: str, retry_after: float):
    """Build the standard 429 response. retry_after is a float seconds;
    we round up to whole seconds for the HTTP header (HTTP requires int)."""
    _maybe_audit(key)
    secs = max(1, int(retry_after + 0.999))
    resp = jsonify({'status': 'error',
                    'message': f'Too many requests. Try again in {secs}s.',
                    'retry_after_s': secs})
    resp.status_code = 429
    resp.headers['Retry-After'] = str(secs)
    return resp


def limit_per_ip(name: str, limit: int = None, window_s: int = 60,
                 settings_key: str = None):
    """Decorate a route. Bucket key = 'ip:<addr>:<name>'.
    If `settings_key` is given, the value at that settings key overrides
    `limit`."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            cfg = _settings()
            if not cfg.get('ratelimit.enabled', True):
                return f(*args, **kwargs)
            actual_limit = (cfg.get(settings_key) if settings_key else None) or limit
            key = f'ip:{_client_ip()}:{name}'
            ok, retry = _check_and_consume(key, actual_limit, window_s)
            if not ok:
                return _refuse(key, retry)
            return f(*args, **kwargs)
        return wrapper
    return decorator


def limit_per_token(name: str, limit: int = None, window_s: int = 60,
                    settings_key: str = None):
    """Decorate an API route. Bucket key prefers token id over IP, so a
    legitimate single client behind a NAT isn't co-limited with others."""
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            cfg = _settings()
            if not cfg.get('ratelimit.enabled', True):
                return f(*args, **kwargs)
            actual_limit = (cfg.get(settings_key) if settings_key else None) or limit
            key = f'{_token_or_ip_key()}:{name}'
            ok, retry = _check_and_consume(key, actual_limit, window_s)
            if not ok:
                return _refuse(key, retry)
            return f(*args, **kwargs)
        return wrapper
    return decorator


# ----------------------------------------------------------------------------
# Test / introspection helpers
# ----------------------------------------------------------------------------

def reset_all():
    """Clear every bucket. ONLY for tests."""
    with _LOCK:
        _BUCKETS.clear()


def bucket_count() -> int:
    """Live bucket count -- exposed for monitoring."""
    with _LOCK:
        return len(_BUCKETS)
