"""HTTP for image sources (stdlib only). The admin typed these addresses, so a source may be on the LAN (a NAS, Nextcloud at home);
a cloud kind must resolve to public addresses. Link-local and unspecified addresses (cloud metadata services) are never allowed.
Redirects are followed by hand (at most 4) and every hop is checked; only http and https; the body is capped while it is read.
"""
from __future__ import annotations

import base64
import http.client
import ipaddress
import socket
import ssl
from urllib.parse import urljoin, urlsplit

USER_AGENT = "AiSignX-ImageSource/3"
TIMEOUT_S = 20
MAX_REDIRECTS = 4


class SourceError(Exception):
    """A failure with a sentence the admin can read (never raw upstream text)."""


def _bad_address(ip: ipaddress._BaseAddress, allow_private: bool) -> bool:
    if ip.is_link_local or ip.is_unspecified or ip.is_multicast or ip.is_reserved:
        return True
    if ip.is_loopback or ip.is_private:
        return not allow_private
    return False


def check_host(host: str, port: int, allow_private: bool) -> str:
    """The address to connect to (checked), or SourceError."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError:
        raise SourceError(f"The address {host} cannot be found.") from None
    chosen = None
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if _bad_address(ip, allow_private):
            raise SourceError(f"{host} is not allowed here (it points to {'a private or local' if ip.is_private or ip.is_loopback else 'a reserved'} address).")
        chosen = chosen or info[4][0]
    if chosen is None:
        raise SourceError(f"The address {host} cannot be found.")
    return chosen


class _Pinned:
    """One connection to an address that was already checked; TLS verifies the host name, not the address."""

    def __init__(self, host: str, ip: str, port: int, tls: bool):
        outer = self

        class _Conn(http.client.HTTPSConnection if tls else http.client.HTTPConnection):
            def connect(self):
                sock = socket.create_connection((outer.ip, outer.port), TIMEOUT_S)
                if tls:
                    sock = self._context.wrap_socket(sock, server_hostname=host)
                self.sock = sock

        self.ip, self.port = ip, port
        kw = {"context": ssl.create_default_context()} if tls else {}
        self._conn = _Conn(host, port, timeout=TIMEOUT_S, **kw)
        self.request, self.getresponse, self.close = self._conn.request, self._conn.getresponse, self._conn.close


def request(method: str, url: str, *, headers: dict | None = None, body: bytes | None = None, allow_private: bool = False,
            auth: tuple[str, str] | None = None, max_bytes: int = 4 * 1024 * 1024, sink=None, same_auth_host: bool = True):
    """-> (status, headers dict (lower-case), body bytes). With `sink` (a file object) the body is written there instead and b'' returned."""
    hdrs = {"User-Agent": USER_AGENT, "Accept": "*/*", "Accept-Encoding": "identity"}
    hdrs.update(headers or {})
    first_host = urlsplit(url).hostname
    for _hop in range(MAX_REDIRECTS + 1):
        u = urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SourceError("Only http and https addresses are allowed.")
        port = u.port or (443 if u.scheme == "https" else 80)
        ip = check_host(u.hostname, port, allow_private)
        h = dict(hdrs)
        if auth and (not same_auth_host or u.hostname == first_host):
            h["Authorization"] = "Basic " + base64.b64encode(f"{auth[0]}:{auth[1]}".encode()).decode()
        try:
            conn = _Pinned(u.hostname, ip, port, u.scheme == "https")
            h["Host"] = u.netloc
            conn.request(method, (u.path or "/") + ("?" + u.query if u.query else ""), body=body, headers=h)
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException, ssl.SSLError):
            raise SourceError(f"Could not connect to {u.hostname}.") from None
        try:
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urljoin(url, resp.getheader("Location"))
                resp.read(0)
                continue
            data = b""
            total = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise SourceError("The answer is larger than allowed.")
                if sink is not None:
                    sink.write(chunk)
                else:
                    data += chunk
            return resp.status, {k.lower(): v for k, v in resp.getheaders()}, data
        except (OSError, http.client.HTTPException):
            raise SourceError(f"The connection to {u.hostname} broke.") from None
        finally:
            conn.close()
    raise SourceError("Too many redirects.")
