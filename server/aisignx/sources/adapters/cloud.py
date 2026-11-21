"""Cloud places that can be read without the owner's password: a shared Google Drive folder, a OneDrive share link, a Dropbox
shared-folder link, and a public Flickr photo stream. Google Photos is not here on purpose: since 2025 its API only shows
pictures an app made itself, so put the album in a shared Drive folder instead."""
from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from urllib.parse import quote, urlencode, urlsplit

from .. import http
from ..http import SourceError
from . import Remote, is_image_name

MAX_IMAGE = 40 * 1024 * 1024
MAX_ZIP = 250 * 1024 * 1024
MAX_JSON = 2 * 1024 * 1024


def _json(url: str, **kw):
    status, _h, body = http.request("GET", url, max_bytes=MAX_JSON, **kw)
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        data = None
    return status, data


def _download(url: str, auth=None):
    def fetch(sink):
        status, _h, _b = http.request("GET", url, max_bytes=MAX_IMAGE, sink=sink, auth=auth, same_auth_host=True)
        if status != 200:
            raise SourceError(f"The service answered {status} for an image.")
    return fetch


# ---- Google Drive: a folder shared with "anyone with the link", read with the owner's free API key ----------------------
_DRIVE_ID = re.compile(r"/folders/([A-Za-z0-9_-]{10,})|[?&]id=([A-Za-z0-9_-]{10,})|^([A-Za-z0-9_-]{10,})$")


def list_gdrive(source, secret, limit: int) -> list[Remote]:
    m = _DRIVE_ID.search(source.location.strip())
    folder = next((g for g in (m.groups() if m else ()) if g), None)
    if not folder:
        raise SourceError("Paste the link of the shared Google Drive folder (or its id).")
    if not secret:
        raise SourceError("A Google API key is needed (free: console.cloud.google.com, turn on the Drive API, create an API key). The folder must be shared with 'anyone with the link'.")
    out: list[Remote] = []
    todo, depth_of = [folder], {folder: 0}
    while todo and len(out) < limit:
        fid = todo.pop(0)
        token = ""
        while len(out) < limit:
            q = {"q": f"'{fid}' in parents and trashed=false", "pageSize": "200", "key": secret, "supportsAllDrives": "true",
                 "includeItemsFromAllDrives": "true", "fields": "nextPageToken,files(id,name,mimeType,size,md5Checksum,modifiedTime)"}
            if token:
                q["pageToken"] = token
            status, data = _json("https://www.googleapis.com/drive/v3/files?" + urlencode(q))
            if status in (400, 403) and isinstance(data, dict):
                reason = ((data.get("error") or {}).get("errors") or [{}])[0].get("reason", "")
                raise SourceError("Google refused the key or the folder is not shared with 'anyone with the link'" + (f" ({reason})." if reason else "."))
            if status == 404:
                raise SourceError("Google Drive does not know this folder.")
            if status != 200 or not isinstance(data, dict):
                raise SourceError(f"Google Drive answered {status}.")
            for f in data.get("files", []):
                mime = f.get("mimeType", "")
                if mime == "application/vnd.google-apps.folder":
                    if source.recursive and depth_of[fid] < 4:
                        todo.append(f["id"]); depth_of[f["id"]] = depth_of[fid] + 1
                elif mime.startswith("image/") and mime in ("image/jpeg", "image/png", "image/gif", "image/webp", "image/bmp"):
                    ext = {"image/jpeg": "jpg", "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/bmp": "bmp"}[mime]
                    name = f.get("name") or f["id"]
                    if not is_image_name(name):
                        name += "." + ext
                    out.append(Remote(key=f["id"], name=name, stamp=(f.get("md5Checksum") or f.get("modifiedTime") or "")[:150],
                                      fetch=_download(f"https://www.googleapis.com/drive/v3/files/{f['id']}?alt=media&key={quote(secret)}"),
                                      size=int(f["size"]) if f.get("size") else None))
                    if len(out) >= limit:
                        break
            token = data.get("nextPageToken") or ""
            if not token:
                break
    return out


# ---- OneDrive: a shared folder link ("anyone with the link can view") -------------------------------------------------
def list_onedrive(source, secret, limit: int) -> list[Remote]:
    link = source.location.strip()
    if not link.lower().startswith("https://"):
        raise SourceError("Paste the OneDrive share link (it starts with https://).")
    share_id = "u!" + base64.urlsafe_b64encode(link.encode()).decode().rstrip("=")
    out: list[Remote] = []

    def walk(url: str, depth: int):
        while url and len(out) < limit:
            status, data = _json(url)
            if status in (401, 403):
                raise SourceError("OneDrive says this link needs a sign-in: share it as 'anyone with the link can view'.")
            if status == 404:
                raise SourceError("OneDrive does not know this link.")
            if status != 200 or not isinstance(data, dict):
                raise SourceError(f"OneDrive answered {status}.")
            for it in data.get("value", []):
                if "folder" in it:
                    if source.recursive and depth < 4:
                        walk(f"https://api.onedrive.com/v1.0/shares/{share_id}/items/{it['id']}/children?$top=200", depth + 1)
                elif "file" in it and is_image_name(it.get("name", "")):
                    dl = it.get("@content.downloadUrl") or f"https://api.onedrive.com/v1.0/shares/{share_id}/items/{it['id']}/content"
                    out.append(Remote(key=it["id"], name=it["name"], stamp=((it.get("file") or {}).get("hashes") or {}).get("sha1Hash")
                                      or (it.get("eTag") or ""), fetch=_download(dl), size=it.get("size")))
                    if len(out) >= limit:
                        return
            url = data.get("@odata.nextLink")

    walk(f"https://api.onedrive.com/v1.0/shares/{share_id}/root/children?$top=200", 0)
    return out


# ---- Dropbox: a shared-folder link; Dropbox hands out the whole folder as one zip --------------------------------------
def list_dropbox(source, secret, limit: int) -> list[Remote]:
    link = source.location.strip()
    host = (urlsplit(link).hostname or "").lower()
    if not link.lower().startswith("https://") or not (host == "dropbox.com" or host.endswith(".dropbox.com")):
        raise SourceError("Paste the Dropbox shared-folder link (https://www.dropbox.com/sh/... or /scl/fo/...).")
    url = re.sub(r"([?&])dl=0", r"\1dl=1", link) if "dl=" in link else link + ("&" if "?" in link else "?") + "dl=1"
    buf = io.BytesIO()
    status, _h, _b = http.request("GET", url, max_bytes=MAX_ZIP, sink=buf)
    if status != 200:
        raise SourceError(f"Dropbox answered {status}: is the link a shared folder that anyone can open?")
    return _zip_items(buf.getvalue(), limit)


def _zip_items(data: bytes, limit: int) -> list[Remote]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise SourceError("The download was not a folder zip (a single file link?).") from None
    out: list[Remote] = []
    total = 0
    infos = sorted((i for i in zf.infolist() if not i.is_dir() and is_image_name(i.filename)), key=lambda i: i.date_time, reverse=True)
    for info in infos[:limit]:
        total += info.file_size
        if info.file_size > MAX_IMAGE or total > 4 * MAX_ZIP:
            continue                                     # a zip bomb or a huge file is skipped, never unpacked

        def fetch(sink, i=info):
            with zf.open(i) as f:
                sink.write(f.read(MAX_IMAGE + 1)[:MAX_IMAGE])
        out.append(Remote(key=info.filename[:600], name=info.filename.rsplit("/", 1)[-1], stamp=f"{info.CRC}-{info.file_size}", fetch=fetch, size=info.file_size))
    return out


# ---- Flickr: the public photo stream of a person or an album (no account needed; Flickr's feed shows the latest 20) ------
def list_flickr(source, secret, limit: int) -> list[Remote]:
    loc = source.location.strip()
    m = re.search(r"(\d{6,}@N\d{2})", loc)
    album = re.search(r"/(?:albums|sets)/(\d{6,})", loc)
    if not m:
        raise SourceError("Enter the Flickr user id (like 12345678@N00) or paste a link that contains it. Find it at flickr.com/services/api/explore/flickr.people.getInfo.")
    q = {"format": "json", "nojsoncallback": "1", "id": m.group(1)}
    url = "https://www.flickr.com/services/feeds/photos_public.gne?"
    if album:
        url = "https://www.flickr.com/services/feeds/photoset.gne?"
        q = {"format": "json", "nojsoncallback": "1", "set": album.group(1), "nsid": m.group(1)}
    status, data = _json(url + urlencode(q))
    if status != 200 or not isinstance(data, dict):
        raise SourceError(f"Flickr answered {status}.")
    out: list[Remote] = []
    for it in (data.get("items") or [])[:limit]:
        small = ((it.get("media") or {}).get("m") or "")
        if not small.startswith("https://") or "staticflickr.com" not in small:
            continue
        big = re.sub(r"_m(\.\w+)$", r"_b\1", small)       # the 1024 px size
        link = it.get("link") or big
        out.append(Remote(key=link[:600], name=(it.get("title") or "Photo").strip()[:100] + ".jpg", stamp=it.get("date_taken", "")[:40], fetch=_download(big)))
    return out
