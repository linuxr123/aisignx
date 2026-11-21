"""One adapter per kind of place: each turns an ImageSource into a list of Remote images.

Remote.fetch(sink) writes the image to `sink`. The sync (sources/sync.py) decides what to download by comparing `key`
and `stamp` with what it imported before, so listing must be cheap and downloading happens only for new or changed images.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

IMAGE_EXT = ("jpg", "jpeg", "png", "gif", "webp", "bmp")


@dataclass
class Remote:
    key: str                                   # stable id inside the source (relative path, URL, file id)
    name: str                                  # a name to show
    stamp: str                                 # changes when the image changes (etag, size+time)
    fetch: Callable                            # fetch(sink) writes the bytes; raises SourceError
    size: int | None = None


def is_image_name(name: str) -> bool:
    return name.rsplit(".", 1)[-1].lower() in IMAGE_EXT if "." in name else False


def adapter_for(kind: str):
    from . import cloud, folder, web
    return {"folder": folder.list_items, "http": web.list_http, "webdav": web.list_webdav, "gdrive": cloud.list_gdrive,
            "onedrive": cloud.list_onedrive, "dropbox": cloud.list_dropbox, "flickr": cloud.list_flickr}[kind]
