"""A web folder: an HTTP directory listing (nginx/Apache index, many NAS web pages) or WebDAV (Nextcloud, ownCloud, most NAS)."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

from .. import http
from ..http import SourceError
from . import Remote, is_image_name

MAX_LISTING = 2 * 1024 * 1024
MAX_IMAGE = 40 * 1024 * 1024


def _auth(source, secret):
    return (source.username, secret or "") if source.username else None


def _fetcher(url, auth, allow_private=True):
    def fetch(sink):
        status, _h, _b = http.request("GET", url, auth=auth, allow_private=allow_private, max_bytes=MAX_IMAGE, sink=sink)
        if status != 200:
            raise SourceError(f"The server answered {status} for an image.")
    return fetch


class _Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.hrefs.append(v)


def list_http(source, secret, limit: int) -> list[Remote]:
    base = source.location.strip()
    if not base.lower().startswith(("http://", "https://")):
        raise SourceError("The address must start with http:// or https://.")
    if not base.endswith("/"):
        base += "/"
    auth = _auth(source, secret)
    out: list[Remote] = []
    seen = set()
    todo = [(base, 0)]
    host = urlsplit(base).netloc
    while todo and len(out) < limit:
        url, depth = todo.pop(0)
        status, _h, body = http.request("GET", url, auth=auth, allow_private=True, max_bytes=MAX_LISTING)
        if status == 401 or status == 403:
            raise SourceError("The server asked for a user name and password (or refused them).")
        if status != 200:
            raise SourceError(f"The server answered {status} for the folder.")
        parser = _Links()
        parser.feed(body.decode("utf-8", "replace"))
        for href in parser.hrefs:
            if href.startswith(("?", "#", "../", "/..")) or href in ("/", "./"):
                continue
            target = urljoin(url, href.split("#")[0])
            t = urlsplit(target)
            if t.netloc != host or not target.startswith(base) or target in seen:
                continue                                        # never leave the folder or the host
            seen.add(target)
            name = unquote(t.path.rsplit("/", 1)[-1])
            if target.endswith("/"):
                if source.recursive and depth < 4:
                    todo.append((target, depth + 1))
            elif is_image_name(name):
                out.append(Remote(key=target[len(base):], name=name, stamp="", fetch=_fetcher(target, auth)))
                if len(out) >= limit:
                    break
    # A listing has no reliable change time: ask for it with a cheap HEAD (etag, else length and date).
    for r in out:
        try:
            status, h, _b = http.request("HEAD", base + r.key, auth=auth, allow_private=True)
            r.stamp = (h.get("etag") or (h.get("content-length", "") + "-" + h.get("last-modified", "")))[:150] if status == 200 else ""
        except SourceError:
            r.stamp = ""
    return out


_DAV = "{DAV:}"


def _dav_target(source):
    """(url, user, password-or-None): a Nextcloud/ownCloud *share link* (…/s/<token>) becomes its public WebDAV address."""
    url = source.location.strip()
    m = re.match(r"^(https?://[^/]+(?:/[^?#]*?)?)/s/([A-Za-z0-9]+)/?(?:[?#].*)?$", url)
    if m:
        return m.group(1) + "/public.php/webdav/", m.group(2)
    return url, None


def list_webdav(source, secret, limit: int) -> list[Remote]:
    url, share_token = _dav_target(source)
    if not url.lower().startswith(("http://", "https://")):
        raise SourceError("The address must start with http:// or https://.")
    if not url.endswith("/"):
        url += "/"
    auth = (share_token, source.username and secret or "") if share_token else _auth(source, secret)
    root_path = urlsplit(url).path
    origin = f"{urlsplit(url).scheme}://{urlsplit(url).netloc}"

    def absolute(h: str) -> str:
        return h if h.startswith("http") else origin + h

    out: list[Remote] = []
    todo = [(url, 0)]
    body = (b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop><d:getetag/><d:getcontentlength/><d:getlastmodified/>'
            b'<d:resourcetype/></d:prop></d:propfind>')
    while todo and len(out) < limit:
        folder, depth = todo.pop(0)
        status, _h, data = http.request("PROPFIND", folder, headers={"Depth": "1", "Content-Type": "application/xml"}, body=body,
                                        auth=auth, allow_private=True, max_bytes=MAX_LISTING)
        if status in (401, 403):
            raise SourceError("The server refused the user name or password.")
        if status != 207:
            raise SourceError(f"The server answered {status}: is this a WebDAV address?")
        try:
            tree = ET.fromstring(data)
        except ET.ParseError:
            raise SourceError("The server's answer was not understood.") from None
        for resp in tree.findall(_DAV + "response"):
            href = resp.findtext(_DAV + "href") or ""
            path = unquote(urlsplit(href).path)
            if path.rstrip("/") == unquote(urlsplit(folder).path).rstrip("/"):
                continue                                         # the folder itself
            if not path.startswith(root_path.rstrip("/") + "/") and path != root_path:
                continue
            prop = resp.find(f"{_DAV}propstat/{_DAV}prop")
            if prop is None:
                continue
            is_dir = prop.find(f"{_DAV}resourcetype/{_DAV}collection") is not None
            name = path.rstrip("/").rsplit("/", 1)[-1]
            if is_dir:
                if source.recursive and depth < 4:
                    todo.append((absolute(href), depth + 1))
            elif is_image_name(name):
                full = absolute(href)
                stamp = (prop.findtext(_DAV + "getetag") or "") or (prop.findtext(_DAV + "getcontentlength") or "") + (prop.findtext(_DAV + "getlastmodified") or "")
                out.append(Remote(key=path[len(root_path):].lstrip("/"), name=name, stamp=stamp.strip('"')[:150], fetch=_fetcher(full, auth),
                                  size=int(prop.findtext(_DAV + "getcontentlength") or 0) or None))
                if len(out) >= limit:
                    break
    return out
