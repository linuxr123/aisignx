"""SSRF-safe plugin HTTP client. No network: DNS is faked, and a loopback test server runs
under the client's test-only loopback allowance."""
import gzip
import logging
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from aisignx.extensions import db
from aisignx.models import Setting
from aisignx.plugins import fetch
from aisignx.plugins.fetch import Buckets, Client, FetchError, RateLimited, address_allowed

from .plugin_fixtures import papp  # noqa: F401

_real_getaddrinfo = socket.getaddrinfo


@pytest.fixture
def dns(monkeypatch):
    """dns["host"] = ["1.2.3.4", ...]; unknown names fail, IP literals pass through."""
    table = {}

    def fake(host, port, *args, **kwargs):
        if host in table:
            return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "",
                     (ip, port)) for ip in table[host]]
        try:
            __import__("ipaddress").ip_address(host)
        except ValueError:
            raise socket.gaierror("no such host") from None
        return _real_getaddrinfo(host, port, *args, **kwargs)

    monkeypatch.setattr(fetch.socket, "getaddrinfo", fake)
    return table


class _Handler(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *args):
        pass

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        if "Content-Length" not in (headers or {}):
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        _Handler.seen.append((self.path, dict(self.headers)))
        port = self.server.server_address[1]
        path = self.path.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path if not path.startswith("/") else path
        if path.startswith("/ok"):
            self._send(200, b'{"ok": true}', {"Content-Type": "application/json"})
        elif path == "/ua":
            self._send(200, self.headers.get("User-Agent", "").encode())
        elif path == "/big":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"x" * 5000)
            self.close_connection = True
        elif path == "/biglen":
            self._send(200, b"", {"Content-Length": "999999"})
        elif path == "/bomb":
            body = gzip.compress(b"\0" * 5_000_000)
            self._send(200, body, {"Content-Encoding": "gzip"})
        elif path == "/gz":
            self._send(200, gzip.compress(b"hello"), {"Content-Encoding": "gzip"})
        elif path == "/slow":
            time.sleep(1.5)
            self._send(200, b"late")
        elif path == "/redir-private":
            self._send(302, headers={"Location": f"http://private.test:{port}/ok"})
        elif path == "/redir-offlist":
            self._send(302, headers={"Location": f"http://elsewhere.test:{port}/ok"})
        elif path == "/redir-ok":
            self._send(302, headers={"Location": "/ok"})
        elif path == "/redir-other":
            self._send(302, headers={"Location": f"http://second.test:{port}/hdr"})
        elif path == "/hdr":
            self._send(200, self.headers.get("X-Key", "none").encode())
        elif path == "/loop":
            self._send(302, headers={"Location": "/loop"})
        else:
            self._send(404)


@pytest.fixture
def server(dns):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.seen = []
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    port = httpd.server_address[1]
    for name in ("test.local", "second.test", "elsewhere.test"):
        dns[name] = ["127.0.0.1"]
    dns["private.test"] = ["10.0.0.5"]
    yield f"http://test.local:{port}"
    httpd.shutdown()
    httpd.server_close()


def client(**kw):
    kw.setdefault("hosts", ("test.local", "second.test", "private.test"))
    kw.setdefault("allow_loopback", True)
    kw.setdefault("max_bytes", 1000)
    kw.setdefault("deadline", time.monotonic() + 5)
    return Client(**kw)


# --- address checks ---------------------------------------------------------------------

@pytest.mark.parametrize("ip", [
    "10.1.2.3", "172.16.0.1", "192.168.1.1", "127.0.0.1", "169.254.169.254", "100.64.0.1",
    "0.0.0.0", "224.0.0.1", "255.255.255.255", "::1", "::", "fe80::1", "fc00::1", "ff02::1",
    "::ffff:10.0.0.1", "::ffff:127.0.0.1",
    "2002:0a00:0001::1",                # 6to4 wrapping 10.0.0.1
    "2001:0:0a00:0001:0:0:f5ff:fffe",   # Teredo, server 10.0.0.1
    "2001:0:808:808:0:0:f5ff:fffe",     # Teredo, client 10.0.0.1 (obfuscated)
    "64:ff9b::a00:1",                   # NAT64 of 10.0.0.1
    "64:ff9b::7f00:1",                  # NAT64 of 127.0.0.1
])
def test_private_and_wrapped_addresses_are_blocked(ip):
    assert not address_allowed(ip)


@pytest.mark.parametrize("ip", ["93.184.216.34", "2606:4700:4700::1111", "::ffff:8.8.8.8",
                                "64:ff9b::808:808"])
def test_public_addresses_pass(ip):
    assert address_allowed(ip)


def test_private_allowance_still_blocks_loopback_and_link_local():
    assert address_allowed("10.0.0.1", allow_private=True)
    assert address_allowed("192.168.0.10", allow_private=True)
    for ip in ("127.0.0.1", "169.254.1.1", "0.0.0.0", "224.0.0.5", "::1"):
        assert not address_allowed(ip, allow_private=True)


def test_any_private_answer_blocks_the_host(dns):
    dns["api.example.com"] = ["93.184.216.34", "10.0.0.1"]
    c = Client(hosts=("api.example.com",), deadline=time.monotonic() + 5)
    with pytest.raises(FetchError) as err:
        c.get("https://api.example.com/x")
    assert err.value.code == "blocked_address"
    dns["api.example.com"] = ["::ffff:192.168.1.1"]
    with pytest.raises(FetchError, match="blocked_address"):
        c.get("https://api.example.com/x")


def test_private_hosts_setting_allows_intranet_but_not_loopback(dns):
    dns["intranet.corp"] = ["10.0.0.7"]
    dns["evil.corp"] = ["127.0.0.1"]
    c = Client(private_hosts=("intranet.corp", "evil.corp"), deadline=time.monotonic() + 0.5)
    with pytest.raises(FetchError) as err:  # passes the check, then cannot connect
        c.get("http://intranet.corp/feed", user_url=True)
    assert err.value.code != "blocked_address"
    c = Client(private_hosts=("intranet.corp", "evil.corp"), deadline=time.monotonic() + 5)
    with pytest.raises(FetchError, match="blocked_address"):
        c.get("http://evil.corp/feed", user_url=True)


# --- request rules ----------------------------------------------------------------------

def test_url_rules(dns):
    c = Client(hosts=("api.example.com",), deadline=time.monotonic() + 5)
    cases = {"https://other.example.com/": "blocked_host", "http://api.example.com/": "bad_scheme",
             "ftp://api.example.com/": "bad_scheme", "https://api.example.com:8443/": "bad_port",
             "https://user:pw@api.example.com/": "bad_url", "https:///nohost": "bad_url"}
    for url, code in cases.items():
        with pytest.raises(FetchError) as err:
            c.get(url)
        assert err.value.code == code, url
    with pytest.raises(FetchError, match="bad_port"):
        c.get("http://feeds.example.org:9000/rss", user_url=True)


def test_ok_gzip_and_user_agent(server):
    c = client(user_agent="AiSignX/test (+x)")
    resp = c.get(server + "/ok")
    assert resp.ok and resp.json() == {"ok": True}
    assert c.get(server + "/gz").body == b"hello"
    assert c.get(server + "/ua").body == b"AiSignX/test (+x)"


def test_size_caps_and_gzip_bomb(server):
    for path in ("/big", "/biglen", "/bomb"):
        with pytest.raises(FetchError) as err:
            client().get(server + path)
        assert err.value.code == "too_large", path


def test_timeout(server):
    with pytest.raises(FetchError, match="timeout"):
        client(deadline=time.monotonic() + 0.5).get(server + "/slow")


def test_redirects(server):
    assert client().get(server + "/redir-ok").json() == {"ok": True}
    with pytest.raises(FetchError, match="blocked_address"):
        client().get(server + "/redir-private")
    with pytest.raises(FetchError, match="blocked_host"):
        client().get(server + "/redir-offlist")
    with pytest.raises(FetchError, match="too_many_redirects"):
        client().get(server + "/loop")
    # Headers (credentials) are not forwarded to another host.
    assert client().get(server + "/redir-other", headers={"X-Key": "k"}).body == b"none"
    assert client().get(server.replace("test.local", "second.test") + "/hdr",
                        headers={"X-Key": "k"}).body == b"k"


def test_rate_limit():
    buckets = Buckets()
    assert buckets.take("h", 1) and not buckets.take("h", 1)
    c = Client(hosts=("api.example.com",), rate={"api.example.com": 1}, buckets=buckets)
    buckets.take("api.example.com", 1)
    with pytest.raises(RateLimited):
        c.get("https://api.example.com/")


def test_redaction_in_urls_and_logs(server, caplog):
    assert "SECRET" not in fetch.redact_url("https://h/x?apikey=SECRET&a=1", ["apikey"])
    assert "a=1" in fetch.redact_url("https://h/x?apikey=SECRET&a=1", ["apikey"])
    caplog.set_level(logging.DEBUG, logger="aisignx.plugins.fetch")
    client().get(server + "/ok?apikey=SECRET123&x=1", redact=["apikey"])
    assert "SECRET123" not in caplog.text and "REDACTED" in caplog.text


def test_environment_proxies_are_ignored(server, monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    assert client().get(server + "/ok").ok


def test_http_proxy_gets_absolute_urls(server):
    port = server.rsplit(":", 1)[1]
    resp = client(proxy=f"http://127.0.0.1:{port}").get(server + "/ok")
    assert resp.ok
    assert _Handler.seen[-1][0] == f"http://test.local:{port}/ok"


def test_https_proxy_uses_connect(dns):
    dns["api.example.com"] = ["93.184.216.34"]
    seen = []
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)

    def proxy():
        conn, _ = listener.accept()
        seen.append(conn.recv(4096).decode("latin-1").split("\r\n", 1)[0])
        conn.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
        conn.close()

    thread = threading.Thread(target=proxy, daemon=True)
    thread.start()
    c = Client(hosts=("api.example.com",), deadline=time.monotonic() + 5,
               proxy=f"http://127.0.0.1:{listener.getsockname()[1]}")
    with pytest.raises(FetchError) as err:
        c.get("https://api.example.com/v1?apikey=zzz", redact=["apikey"])
    thread.join(2)
    listener.close()
    assert seen == ["CONNECT api.example.com:443 HTTP/1.1"]
    assert err.value.code == "proxy_error"


def test_user_agent_format(papp):
    from aisignx import __version__
    from aisignx.plugins import service

    with papp.app_context():
        assert service.user_agent() == (
            f"AiSignX/{__version__} (+https://github.com/linuxr123/aisignx)")
        Setting.set("plugins.ua_contact", "it@example.com")
        db.session.commit()
        assert service.user_agent().endswith("; it@example.com)")
