"""A folder on the server's disk, including a network share the server can read (a UNC path such as \\\\nas\\photos, or a mounted share)."""
from __future__ import annotations

import os
from pathlib import Path

from ..http import SourceError
from . import Remote, is_image_name

MAX_DEPTH = 8


def list_items(source, secret, limit: int) -> list[Remote]:
    root_text = (source.location or "").strip()
    if not root_text:
        raise SourceError("Enter the folder.")
    root = Path(root_text)
    try:
        root = root.resolve(strict=True)
    except (OSError, RuntimeError):
        raise SourceError(f"The folder {root_text} cannot be found or read by the server.") from None
    if not root.is_dir():
        raise SourceError(f"{root_text} is not a folder.")
    found: list[tuple[float, Remote]] = []

    def add(path: Path):
        try:
            st = path.stat()
        except OSError:
            return
        rel = path.relative_to(root).as_posix()

        def fetch(sink, p=path):
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(1 << 20)
                    if not chunk:
                        break
                    sink.write(chunk)
        found.append((st.st_mtime, Remote(key=rel, name=path.name, stamp=f"{st.st_size}-{int(st.st_mtime)}", fetch=fetch, size=st.st_size)))

    def walk(folder: Path, depth: int):
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name.lower())
        except OSError:
            return
        for e in entries:
            try:
                p = Path(e.path)
                if e.is_symlink():                      # never follow a link out of the folder
                    target = p.resolve()
                    if root not in target.parents and target != root:
                        continue
                if e.is_dir() and source.recursive and depth < MAX_DEPTH:
                    walk(p, depth + 1)
                elif e.is_file() and is_image_name(e.name):
                    add(p)
            except OSError:
                continue
            if len(found) >= limit * 3:                 # enough to pick the newest from
                return

    walk(root, 0)
    found.sort(key=lambda t: t[0], reverse=True)         # newest first: the limit keeps the newest
    return [r for _t, r in found[:limit]]
