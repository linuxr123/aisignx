"""SSRF-safe HTTP client for plugin fetches (stdlib only: http.client, ssl, socket).

Every request:
    - goes to a host on the plugin's allowlist (exact names), or, for URLs a user typed
      (RSS feeds and their images: user_url=True), to any public host;
    - uses https on port 443; user URLs may also use http and ports 80, 8080 and 8443;
    - resolves the host once, checks EVERY address (IPv4-mapped, 6to4, Teredo and NAT64
      forms are unwrapped and the IPv4 inside is checked too) and connects to the checked
      address, so DNS rebinding cannot swap it; TLS still verifies the host name;
    - follows at most 3 redirects by hand, checking every hop again;
    - stops at the plugin's max_bytes (also after gzip decoding) and at the deadline;
    - takes a token from the per-host rate bucket, else raises RateLimited.

Errors are FetchError codes (timeout, http_503, blocked_address, too_large,
parse_error, ...), never upstream text. Query parameters named in `redact` are masked
in every logged URL. Environment proxies are ignored; AISIGNX_PLUGIN_HTTP_PROXY sends
requests through one proxy (HTTPS via CONNECT). Through a proxy the address check is
best effort: it runs on whatever this machine can resolve, and the proxy connects.
"""
import http.client
import ipaddress
import json
import logging
import socket
import ssl
import threading
import time
import zlib
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

from .registry import HOST_RE

log = logging.getLogger(__name__)

CONNECT_TIMEOUT_S = 5.0
DNS_TIMEOUT_S = 5.0
MAX_REDIRECTS = 3
CHUNK = 65536
DEFAULT_RATE = 60       # requests per minute for an allowlisted host without a rate
USER_URL_RATE = 6       # per minute for hosts from user-entered URLs (RSS)
USER_URL_PORTS = (80, 443, 8080, 8443)
_REDIRECTS = (301, 302, 303, 307, 308)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
REDACTED = "REDACTED"


class FetchError(Exception):
    """A failed fetch; `code` is safe to store and show (never upstream text)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class RateLimited(FetchError):
    def __init__(self):
        super().__init__("rate_limited")


@dataclass
class Response:
    status: int
    headers: dict  # lower-case names
    body: bytes = field(repr=False)
    url: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def raise_for_status(self) -> "Response":
        if not self.ok:
            raise FetchError(f"http_{self.status}")
        return self

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            raise FetchError("parse_error") from None

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


# --- addresses --------------------------------------------------------------------------

def _candidates(ip) -> list:
    """The address plus every IPv4 address embedded in it."""
    out = [ip]
    if ip.version == 6:
        if ip.ipv4_mapped:
            out.append(ip.ipv4_mapped)
        if ip.sixtofour:
            out.append(ip.sixtofour)
        if ip.teredo:
            out.extend(ip.teredo)
        if ip in _NAT64:
            out.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return out


def address_allowed(address, allow_private: bool = False, allow_loopback: bool = False) -> bool:
    """Whether a resolved address may be contacted. Loopback, link-local, unspecified,
    multicast and reserved addresses are always refused (loopback only passes under the
    tests' allowance); anything else that is not global needs allow_private."""
    try:
        ip = ipaddress.ip_address(str(address).split("%", 1)[0])
    except ValueError:
        return False
    for candidate in _candidates(ip):
        if candidate.version == 6 and candidate in _NAT64:
            continue  # judged by the IPv4 address inside (also in the list)
        if candidate.is_loopback:
            if allow_loopback:
                continue
            return False
        if (candidate.is_link_local or candidate.is_unspecified or candidate.is_multicast
                or candidate.is_reserved):
            return False
        if not allow_private and not candidate.is_global:
            return False
    return True


def redact_url(url: str, names) -> str:
    """url with the values of the named query parameters masked (for logs)."""
    names = {n.lower() for n in names or ()}
    if not names:
        return url
    parts = urlsplit(url)
    query = [(k, REDACTED if k.lower() in names else v)
             for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(parts._replace(query=urlencode(query)))


# --- rate limits ------------------------------------------------------------------------

class TokenBucket:
    """per_min tokens per minute, bursting up to per_min."""

    def __init__(self, per_min: int):
        self.per_min = max(1, int(per_min))
        self.tokens = float(self.per_min)
        self.stamp = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> bool:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.per_min, self.tokens + (now - self.stamp) * self.per_min / 60)
            self.stamp = now
            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False


class Buckets:
    """Per-host buckets for one process (one instance per app)."""

    def __init__(self):
        self._buckets: dict = {}
        self._lock = threading.Lock()

    def take(self, host: str, per_min: int) -> bool:
        with self._lock:
            bucket = self._buckets.get(host)
            if bucket is None or bucket.per_min != per_min:
                bucket = self._buckets[host] = TokenBucket(per_min)
        return bucket.take()


# --- connections ------------------------------------------------------------------------

class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, ip, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip or self.host, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, ip, timeout, context):
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip or self.host, self.port), self.timeout)
        server_hostname = self.host
        if self._tunnel_host:  # through the proxy: CONNECT first, then TLS to the target
            self.sock = sock
            self._tunnel()
            sock, server_hostname = self.sock, self._tunnel_host
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


def _set_timeout(sock, seconds: float) -> None:
    """Tighten the read timeout to the deadline. http.client may already have released
    the socket object to the response (Connection: close); the timeout set before
    getresponse() then still applies, and the deadline is checked between reads."""
    try:
        sock.settimeout(seconds)
    except OSError:
        pass


def _resolve(host: str, port: int, timeout: float) -> list[str]:
    """Every address of host, looked up on a helper thread so the deadline holds."""
    try:
        return [str(ipaddress.ip_address(host.strip("[]")))]
    except ValueError:
        pass
    box = {}

    def run():
        try:
            box["value"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            box["error"] = exc

    worker = threading.Thread(target=run, name="aisignx-dns", daemon=True)
    worker.start()
    worker.join(max(0.01, timeout))
    if worker.is_alive():
        raise FetchError("timeout")
    if "error" in box or not box.get("value"):
        raise FetchError("dns_error")
    out = []
    for info in box["value"]:
        address = str(info[4][0]).split("%", 1)[0]
        if address not in out:
            out.append(address)
    return out


# --- client -----------------------------------------------------------------------------

class Client:
    """One plugin's HTTP client: its host allowlist, rate limits, size cap and deadline."""

    def __init__(self, *, hosts=(), rate=None, max_bytes: int = 2 * 1024 * 1024,
                 deadline: float | None = None, user_agent: str = "AiSignX",
                 proxy: str | None = None, private_hosts=(), buckets: Buckets | None = None,
                 allow_loopback: bool = False):
        self.hosts = frozenset(h.lower() for h in hosts)
        self.rate = dict(rate or {})
        self.max_bytes = int(max_bytes)
        self.deadline = deadline if deadline is not None else time.monotonic() + 15
        self.user_agent = user_agent
        self.proxy = urlsplit(proxy) if proxy else None
        self.private_hosts = frozenset(h.lower() for h in private_hosts)
        self.buckets = buckets or Buckets()
        self.allow_loopback = allow_loopback  # tests only (PLUGIN_FETCH_TEST_LOOPBACK)
        self._context = ssl.create_default_context()

    def _remaining(self) -> float:
        left = self.deadline - time.monotonic()
        if left <= 0:
            raise FetchError("timeout")
        return left

    def _check_url(self, url: str, user_url: bool, extra_hosts) -> tuple:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise FetchError("bad_url") from None
        scheme, host = parts.scheme.lower(), (parts.hostname or "").lower()
        if not host or parts.username is not None or parts.password is not None:
            raise FetchError("bad_url")
        if scheme not in ("https", "http") or (
                scheme == "http" and not (user_url or self.allow_loopback)):
            raise FetchError("bad_scheme")
        port = port or (443 if scheme == "https" else 80)
        allowed_ports = USER_URL_PORTS if user_url else (443, 80)
        if port not in allowed_ports and not self.allow_loopback:
            raise FetchError("bad_port")
        if not user_url and host not in self.hosts and host not in extra_hosts:
            raise FetchError("blocked_host")
        return scheme, host, port, parts

    def _addresses(self, host: str, port: int) -> list[str]:
        allow_private = host in self.private_hosts
        try:
            addresses = _resolve(host, port, min(DNS_TIMEOUT_S, self._remaining()))
        except FetchError as exc:
            if self.proxy is not None and exc.code == "dns_error":
                return []  # best effort: only the proxy can resolve it
            raise
        for address in addresses:
            if not address_allowed(address, allow_private, self.allow_loopback):
                raise FetchError("blocked_address")
        return addresses

    def get(self, url: str, *, headers: dict | None = None, redact=(), user_url: bool = False,
            allow_hosts=(), max_bytes: int | None = None) -> Response:
        """GET url and return the final Response (any status; see raise_for_status()).

        allow_hosts: exact host names allowed for this call besides the plugin's own
        (an admin-configured tile server, say). user_url: a URL a user typed.
        """
        extra = frozenset(h.lower() for h in allow_hosts if HOST_RE.match(h.lower()))
        cap = int(max_bytes or self.max_bytes)
        headers = dict(headers or {})
        first_host = None
        for _hop in range(MAX_REDIRECTS + 1):
            scheme, host, port, parts = self._check_url(url, user_url, extra)
            if first_host is None:
                first_host = host
            elif host != first_host:
                headers = {}  # never forward credentials to another host
            per_min = self.rate.get(host) or (USER_URL_RATE if user_url and host not in
                                              self.hosts else DEFAULT_RATE)
            if not self.buckets.take(host, per_min):
                raise RateLimited()
            addresses = self._addresses(host, port)
            resp = self._request(scheme, host, port, parts, addresses, headers, cap,
                                 redact_url(url, redact))
            if resp.status in _REDIRECTS and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            resp.url = url
            return resp
        raise FetchError("too_many_redirects")

    def _connection(self, scheme, host, port, ip, timeout):
        if self.proxy is not None:
            phost, pport = self.proxy.hostname, self.proxy.port or 8080
            if scheme == "https":
                conn = _PinnedHTTPS(phost, pport, None, timeout, self._context)
                conn.set_tunnel(host, port)
                return conn, False
            return _PinnedHTTP(phost, pport, None, timeout), True
        if scheme == "https":
            return _PinnedHTTPS(host, port, ip, timeout, self._context), False
        return _PinnedHTTP(host, port, ip, timeout), False

    def _request(self, scheme, host, port, parts, addresses, headers, cap, shown_url):
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        sent = {"User-Agent": self.user_agent, "Accept-Encoding": "gzip",
                "Accept": "*/*", "Connection": "close"}
        sent.update({k: v for k, v in headers.items() if k.lower() not in ("host",)})
        last_error = FetchError("connect_error")
        for ip in addresses or [None]:
            timeout = min(CONNECT_TIMEOUT_S, self._remaining())
            conn, absolute = self._connection(scheme, host, port, ip, timeout)
            try:
                target = urlunsplit((scheme, parts.netloc, path, "", "")) if absolute else path
                conn.request("GET", target, headers=sent)
                sock = conn.sock  # getresponse() may hand the socket to the response
                sock.settimeout(self._remaining())
                raw = conn.getresponse()
                status = raw.status
                resp_headers = {k.lower(): v for k, v in raw.getheaders()}
                body = b"" if status in _REDIRECTS else self._read(raw, sock, resp_headers, cap)
                log.debug("plugin fetch %s -> %s (%d bytes)", shown_url, status, len(body))
                return Response(status, resp_headers, body)
            except FetchError:
                raise
            except (socket.timeout, TimeoutError):
                raise FetchError("timeout") from None
            except ssl.SSLError:
                raise FetchError("tls_error") from None
            except http.client.HTTPException:
                raise FetchError("bad_response") from None
            except OSError as exc:
                if self.proxy is not None and "Tunnel" in str(exc):
                    raise FetchError("proxy_error") from None
                last_error = FetchError("connect_error")
                log.debug("plugin fetch %s: connect to %s failed", shown_url, ip)
            finally:
                conn.close()
        raise last_error

    def _read(self, raw, sock, headers: dict, cap: int) -> bytes:
        encoding = (headers.get("content-encoding") or "identity").strip().lower()
        if encoding not in ("identity", "gzip", "x-gzip"):
            raise FetchError("bad_encoding")
        try:
            if int(headers.get("content-length") or 0) > cap:
                raise FetchError("too_large")
        except ValueError:
            raise FetchError("bad_response") from None
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS) if encoding != "identity" else None
        out, received = bytearray(), 0
        try:
            while True:
                _set_timeout(sock, self._remaining())
                chunk = raw.read(CHUNK)
                if not chunk:
                    break
                received += len(chunk)
                if received > cap:
                    raise FetchError("too_large")
                if inflater is None:
                    out += chunk
                    continue
                out += inflater.decompress(chunk, cap + 1 - len(out))
                while inflater.unconsumed_tail and len(out) <= cap:
                    out += inflater.decompress(inflater.unconsumed_tail, cap + 1 - len(out))
                if len(out) > cap:
                    raise FetchError("too_large")
            if inflater is not None:
                out += inflater.flush()
        except zlib.error:
            raise FetchError("parse_error") from None
        if len(out) > cap:
            raise FetchError("too_large")
        return bytes(out)
