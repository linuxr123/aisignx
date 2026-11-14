"""WP2: failed sign-in rate limiting and the auth audit rows."""
import json

import pytest

from aisignx.auth import ratelimit
from aisignx.auth.ratelimit import FailureLimiter
from aisignx.models import AuditLog

from .conftest import PASSWORD, make_user


def _post(client, username, password, ip="10.0.0.1"):
    return client.post("/login", data={"username": username, "password": password},
                       environ_base={"REMOTE_ADDR": ip})


def _rows(app, action):
    with app.app_context():
        return AuditLog.query.filter_by(action=action).order_by(AuditLog.id).all()


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(ratelimit.time, "monotonic", c)
    return c


# --- the limiter itself --------------------------------------------------------------------

def test_limiter_blocks_at_limit_and_reports_wait(clock):
    lim = FailureLimiter(3, 60)
    assert lim.retry_after("k") is None
    assert lim.fail("k") is False
    clock.now += 10
    assert lim.fail("k") is False
    assert lim.retry_after("k") is None
    clock.now += 10
    assert lim.fail("k") is True
    # The first failure (at t=0) leaves the window at t=60; we are at t=20.
    assert lim.retry_after("k") == 40
    assert lim.retry_after("other") is None


def test_limiter_window_expires(clock):
    lim = FailureLimiter(2, 60)
    lim.fail("k")
    lim.fail("k")
    assert lim.retry_after("k") == 60
    clock.now += 59.5
    assert lim.retry_after("k") == 1
    clock.now += 0.5
    assert lim.retry_after("k") is None
    assert lim.fail("k") is False  # only one failure left in the window


def test_limiter_reset(clock):
    lim = FailureLimiter(1, 60)
    lim.fail("k")
    assert lim.retry_after("k")
    lim.reset("k")
    assert lim.retry_after("k") is None


def test_limiter_key_cap_evicts_oldest(clock):
    lim = FailureLimiter(1, 600, max_keys=3)
    for key in ("a", "b", "c"):
        lim.fail(key)
    lim.retry_after("a")  # touching "a" makes "b" the least recently used
    lim.fail("d")
    assert len(lim) == 3
    assert lim.retry_after("b") is None
    assert lim.retry_after("a") and lim.retry_after("c") and lim.retry_after("d")


def test_limiter_sweeps_idle_keys(clock):
    lim = FailureLimiter(5, 60)
    for i in range(50):
        lim.fail(f"k{i}")
    assert len(lim) == 50
    clock.now += 120
    lim.fail("fresh")
    assert len(lim) == 1


def test_limiter_should_log_once_per_minute(clock):
    lim = FailureLimiter(1, 600)
    assert lim.should_log("k") is True
    assert lim.should_log("k") is False
    clock.now += 61
    assert lim.should_log("k") is True


def test_limiter_disabled_with_zero_limit():
    lim = FailureLimiter(0, 60)
    for _ in range(10):
        assert lim.fail("k") is False
    assert lim.retry_after("k") is None


def test_limiters_are_per_app(app, configured):
    with app.app_context():
        lims = ratelimit.limiters()
        assert lims is ratelimit.limiters()
        assert lims["user"].limit == app.config["LOGIN_MAX_PER_USER"]
        assert lims["ip"].limit == app.config["LOGIN_MAX_PER_IP"]


def test_api_helpers_use_ip_bucket(app):
    app.config["LOGIN_MAX_PER_IP"] = 2
    with app.app_context():
        assert ratelimit.api_blocked("1.2.3.4") is None
        ratelimit.api_failed("1.2.3.4")
        ratelimit.api_failed("1.2.3.4")
        assert ratelimit.api_blocked("1.2.3.4") > 0
        assert ratelimit.api_blocked("1.2.3.5") is None


# --- the login form ------------------------------------------------------------------------

def test_successful_logins_never_count(app, client, configured):
    make_user(app, "alice", "viewer")
    for _ in range(8):
        assert _post(client, "alice", PASSWORD).status_code == 302
        client.post("/logout")
    assert _post(client, "alice", "wrong").status_code == 401


def test_user_limit_across_ips(app, client, configured):
    make_user(app, "alice", "viewer")
    for i in range(4):
        assert _post(client, "alice", "wrong", ip=f"10.0.0.{i}").status_code == 401
    # The fifth failure is still a normal 401; it fills the bucket.
    assert _post(client, "Alice ", "wrong", ip="10.0.0.9").status_code == 401
    resp = _post(client, "alice", PASSWORD, ip="10.0.0.50")  # even the right password
    assert resp.status_code == 429
    assert int(resp.headers["Retry-After"]) > 0
    assert b"Too many failed sign-in attempts" in resp.data
    assert b"15 minutes" in resp.data


def test_blocked_response_same_for_unknown_user(app, client, configured, clock):
    make_user(app, "alice", "viewer")
    for name in ("alice", "ghost"):
        for _ in range(5):
            _post(client, name, "wrong", ip="10.0.1.1" if name == "alice" else "10.0.2.1")
    known = _post(client, "alice", "x", ip="10.0.3.1")
    unknown = _post(client, "ghost", "x", ip="10.0.4.1")
    assert known.status_code == unknown.status_code == 429
    assert known.headers["Retry-After"] == unknown.headers["Retry-After"]
    assert known.data.replace(b"alice", b"?") == unknown.data.replace(b"ghost", b"?")


def test_ip_limit_across_usernames(app, client, configured):
    app.config["LOGIN_MAX_PER_IP"] = 6
    for i in range(6):
        assert _post(client, f"user{i}", "wrong", ip="192.0.2.7").status_code == 401
    assert _post(client, "admin", PASSWORD, ip="192.0.2.7").status_code == 429
    assert _post(client, "admin", PASSWORD, ip="192.0.2.8").status_code == 302


def test_success_clears_user_bucket(app, client, configured):
    make_user(app, "alice", "viewer")
    for _ in range(4):
        _post(client, "alice", "wrong")
    assert _post(client, "alice", PASSWORD).status_code == 302
    client.post("/logout")
    for _ in range(4):
        assert _post(client, "alice", "wrong", ip="10.0.9.9").status_code == 401
    assert _post(client, "alice", PASSWORD, ip="10.0.9.9").status_code == 302


def test_login_window_expires(app, client, configured, clock):
    make_user(app, "alice", "viewer")
    for _ in range(5):
        _post(client, "alice", "wrong")
    assert _post(client, "alice", PASSWORD).status_code == 429
    clock.now += app.config["LOGIN_WINDOW_SECONDS"] + 1
    assert _post(client, "alice", PASSWORD).status_code == 302


# --- audit rows ----------------------------------------------------------------------------

def test_audit_rows_for_login_flow(app, client, configured, clock):
    alice_id = make_user(app, "alice", "viewer")
    _post(client, "Alice", "wrong", ip="10.1.1.1")
    failed = _rows(app, "auth.login_failed")
    assert len(failed) == 1
    assert json.loads(failed[0].detail) == {"username": "alice"}
    assert failed[0].user_id is None and failed[0].ip == "10.1.1.1" and failed[0].via == "web"

    assert _post(client, "alice", PASSWORD).status_code == 302
    (ok,) = _rows(app, "auth.login")
    assert ok.user_id == alice_id and ok.username == "alice"
    assert ok.target_type == "user" and ok.target_id == alice_id

    client.post("/logout")
    (out,) = _rows(app, "auth.logout")
    assert out.user_id == alice_id

    for _ in range(5):
        _post(client, "alice", "wrong", ip="10.1.1.2")
    for _ in range(3):
        assert _post(client, "alice", "wrong", ip="10.1.1.2").status_code == 429
    limited = _rows(app, "auth.rate_limited")
    assert len(limited) == 1  # logged at most once a minute per key
    assert json.loads(limited[0].detail) == {"username": "alice", "scope": "user"}
    clock.now += 61
    _post(client, "alice", "wrong", ip="10.1.1.2")
    assert len(_rows(app, "auth.rate_limited")) == 2
    # Blocked attempts are not counted as failures.
    assert len(_rows(app, "auth.login_failed")) == 6


def test_audit_row_for_password_change(app, client, configured):
    alice_id = make_user(app, "alice", "viewer")
    _post(client, "alice", PASSWORD)
    new = "a-brand-new-password"
    client.post("/profile", data={"current_password": PASSWORD, "password": new, "confirm": new})
    (row,) = _rows(app, "auth.password_change")
    assert row.user_id == alice_id and row.target_id == alice_id
    assert row.detail is None
