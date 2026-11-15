"""File cache for plugin data: stale-while-revalidate, single flight, backoff, prune.

Layout under PLUGIN_CACHE_DIR (never part of a backup; backups hold only the database
and uploads/media|thumbs):

    <key>.json     {fetched_at, expires_at, ttl, last_read_at, last_error_code,
                    backoff_until, fail_count, etag, data, assets, prev_assets}
    sub-<key>.json  values shared across items through ctx.cache() ({value, expires_at})
    assets/<id>    asset bytes; id = sha256(bytes)[:32]

Writes are atomic (a temporary file, then os.replace), so the last good copy survives a
crash or restart. The folder is created on the first write.

serve() is what request threads call. It never waits on an upstream server for more
than WAIT_S: a fresh copy is returned at once; an expired copy is returned at once and
a refresh is queued on the worker (unless one is running or the key is backing off); a
cold miss queues the fetch, waits up to WAIT_S, then answers "warming".
"""
import hashlib
import json
import logging
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, current_app

log = logging.getLogger(__name__)

EXT_KEY = "aisignx_plugin_cache"
WAIT_S = 2.0
WARMING_RETRY_S = 5
BACKOFF_MIN_S = 60
BACKOFF_MAX_S = 900
PRUNE_IDLE_S = 7 * 86400
READ_STAMP_EVERY_S = 3600  # how often last_read_at is written back to disk
TMP_MAX_AGE_S = 3600
ASSET_MIMES = ("image/png", "image/jpeg", "image/webp")


@dataclass
class Served:
    """What serve() found. status: "ok", "warming" or "upstream_unavailable"."""
    status: str
    entry: dict | None = None
    stale: bool = False
    retry_after: int = 0


class PluginCache:
    def __init__(self, root, max_mb: int):
        self.root = Path(root)
        self.max_bytes = max(1, int(max_mb)) * 1024 * 1024
        self._locks: dict = {}
        self._locks_guard = threading.Lock()
        self._inflight: dict = {}  # key -> worker Task
        self._inflight_guard = threading.Lock()
        self._backoff: dict = {}  # key -> (until, fail_count, code) for keys without a copy
        self._read_stamps: dict = {}

    # --- files --------------------------------------------------------------------------

    @property
    def assets_dir(self) -> Path:
        return self.root / "assets"

    def _path(self, key: str, sub: bool = False) -> Path:
        return self.root / (f"sub-{key}.json" if sub else f"{key}.json")

    def _write_json(self, path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
        tmp.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, path)

    def _read_json(self, path: Path) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def read(self, key: str) -> dict | None:
        """The stored entry, or None."""
        entry = self._read_json(self._path(key))
        return entry if entry is not None and "data" in entry else None

    def mark_read(self, key: str, entry: dict) -> None:
        """Record a read (on disk at most once an hour, for prune)."""
        now = time.time()
        if now - float(entry.get("last_read_at") or 0) < READ_STAMP_EVERY_S:
            return
        if now - self._read_stamps.get(key, 0) < READ_STAMP_EVERY_S:
            return
        self._read_stamps[key] = now
        with self.lock(key):
            current = self.read(key)
            if current is not None:
                current["last_read_at"] = now
                self._write_json(self._path(key), current)

    def write_asset(self, asset_id: str, data: bytes) -> None:
        path = self.assets_dir / asset_id
        if path.is_file():
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{asset_id}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(data)
        os.replace(tmp, path)

    def asset_path(self, asset_id: str) -> Path:
        return self.assets_dir / asset_id

    # --- success and failure ------------------------------------------------------------

    def store_success(self, key: str, data, assets: list, ttl: int) -> dict:
        """Write a fresh entry; assets: [(id, mime, bytes)]."""
        for asset_id, _mime, raw in assets:
            self.write_asset(asset_id, raw)
        now = time.time()
        old = self.read(key) or {}
        listed = [{"id": a, "mime": m, "size": len(raw)} for a, m, raw in assets]
        body = json.dumps([data, listed], sort_keys=True, separators=(",", ":"))
        entry = {
            "fetched_at": now, "expires_at": now + ttl, "ttl": ttl, "last_read_at": now,
            "last_error_code": None, "backoff_until": 0, "fail_count": 0,
            "etag": hashlib.sha256(body.encode("utf-8")).hexdigest()[:32],
            "data": data, "assets": listed,
        }
        # The previous asset list stays servable while screens still show the old data.
        prev = old.get("assets") or []
        entry["prev_assets"] = old.get("prev_assets") or [] if prev == listed else prev
        self._write_json(self._path(key), entry)
        self._backoff.pop(key, None)
        return entry

    def store_failure(self, key: str, code: str) -> float:
        """Record a failed fetch; returns the backoff end (epoch seconds)."""
        now = time.time()
        entry = self.read(key)
        state = self._backoff.get(key)
        count = (entry.get("fail_count") or 0) if entry else (state[1] if state else 0)
        count += 1
        until = now + min(BACKOFF_MAX_S, BACKOFF_MIN_S * 2 ** (count - 1))
        if entry is not None:
            entry.update(last_error_code=code, backoff_until=until, fail_count=count)
            self._write_json(self._path(key), entry)
        self._backoff[key] = (until, count, code)
        return until

    def backoff_until(self, key: str, entry: dict | None) -> float:
        state = self._backoff.get(key)
        values = [state[0]] if state else []
        if entry:
            values.append(float(entry.get("backoff_until") or 0))
        return max(values or [0.0])

    # --- shared sub-values (ctx.cache) --------------------------------------------------

    def sub_get(self, key: str):
        """(value, fresh) or (None, False) when nothing is stored."""
        entry = self._read_json(self._path(key, sub=True))
        if entry is None or "value" not in entry:
            return None, False
        now = time.time()
        if now - float(entry.get("last_read_at") or 0) > READ_STAMP_EVERY_S:
            entry["last_read_at"] = now
            self._write_json(self._path(key, sub=True), entry)
        return entry["value"], now < float(entry.get("expires_at") or 0)

    def sub_put(self, key: str, value, ttl: int) -> None:
        now = time.time()
        self._write_json(self._path(key, sub=True),
                         {"value": value, "expires_at": now + ttl, "last_read_at": now})

    # --- single flight ------------------------------------------------------------------

    def submit(self, key: str, fn, *args):
        """Queue fn(*args) on the worker unless a refresh of key is running. Returns the
        running or new Task, or None when the queue is full."""
        from .worker import Task, get_worker

        with self._inflight_guard:
            task = self._inflight.get(key)
            if task is not None and not task.done.is_set():
                return task
            task = Task(self._finish, (key, fn, args))
            self._inflight[key] = task
        if not get_worker().submit_task(task):
            with self._inflight_guard:
                if self._inflight.get(key) is task:
                    del self._inflight[key]
            return None
        return task

    def _finish(self, key, fn, args):
        try:
            return fn(*args)
        finally:
            # The Task in _inflight[key] is this one: submit() adds another only once
            # the running one is done, and done is set after this returns.
            with self._inflight_guard:
                self._inflight.pop(key, None)

    def serve(self, key: str, refresh, wait: float = WAIT_S) -> Served:
        """Stale-while-revalidate read. refresh() runs on the worker for this key."""
        entry = self.read(key)
        now = time.time()
        if entry is not None:
            self.mark_read(key, entry)
            expires = float(entry.get("expires_at") or 0)
            if now < expires:
                return Served("ok", entry)
            stale = now > expires + float(entry.get("ttl") or 0)
            if now >= self.backoff_until(key, entry):
                task = self.submit(key, refresh)
                if task is not None and task.done.is_set():  # ran inline (tests)
                    entry = self.read(key) or entry
                    stale = stale and time.time() >= float(entry.get("expires_at") or 0)
            return Served("ok", entry, stale=stale)
        until = self.backoff_until(key, None)
        if now < until:
            return Served("upstream_unavailable", retry_after=max(1, int(until - now + 0.999)))
        task = self.submit(key, refresh)
        if task is not None:
            task.wait(wait)
        entry = self.read(key)
        if entry is not None:
            return Served("ok", entry)
        until = self.backoff_until(key, None)
        if time.time() < until:
            return Served("upstream_unavailable",
                          retry_after=max(1, int(until - time.time() + 0.999)))
        return Served("warming", retry_after=WARMING_RETRY_S)

    # --- housekeeping -------------------------------------------------------------------

    def _entries(self):
        if not self.root.is_dir():
            return []
        return [p for p in self.root.iterdir() if p.is_file() and p.suffix == ".json"]

    def size_bytes(self) -> int:
        if not self.root.is_dir():
            return 0
        total = 0
        for path in self.root.rglob("*"):
            try:
                total += path.stat().st_size if path.is_file() else 0
            except OSError:
                pass
        return total

    def clear(self) -> int:
        """Remove every entry and asset. Returns the number of files removed."""
        removed = 0
        if not self.root.is_dir():
            return 0
        for path in sorted(self.root.rglob("*"), reverse=True):
            try:
                if path.is_file():
                    path.unlink()
                    removed += 1
                elif path.is_dir():
                    path.rmdir()
            except OSError as exc:
                log.warning("could not remove %s: %s", path.name, exc)
        self._backoff.clear()
        self._read_stamps.clear()
        return removed

    def prune(self, now: float | None = None) -> str:
        """Drop entries not read for 7 days, assets nothing references, stale temporary
        files, then the least recently read entries until the folder fits the cap."""
        now = time.time() if now is None else now
        removed = 0
        if not self.root.is_dir():
            return "nothing cached"
        live = []
        for path in self._entries():
            entry = self._read_json(path) or {}
            last = float(entry.get("last_read_at") or entry.get("fetched_at") or 0)
            if now - last > PRUNE_IDLE_S:
                removed += _unlink(path)
            else:
                live.append((last, path, entry))
        for path in self.root.rglob(".*.tmp"):
            if _age(path, now) > TMP_MAX_AGE_S:
                removed += _unlink(path)
        removed += self._drop_unreferenced(live)
        live.sort(key=lambda t: t[0])
        while live and self.size_bytes() > self.max_bytes:
            _last, path, _entry = live.pop(0)
            removed += _unlink(path)
            removed += self._drop_unreferenced(live)
        return f"removed {removed} file(s); {self.size_bytes() // 1024} KB cached"

    def _drop_unreferenced(self, live) -> int:
        if not self.assets_dir.is_dir():
            return 0
        keep = set()
        for _last, _path, entry in live:
            for listed in (entry.get("assets") or [], entry.get("prev_assets") or []):
                keep.update(a.get("id") for a in listed if isinstance(a, dict))
        removed = 0
        for path in self.assets_dir.iterdir():
            if path.is_file() and not path.name.startswith(".") and path.name not in keep:
                removed += _unlink(path)
        return removed


def _age(path: Path, now: float) -> float:
    try:
        return now - path.stat().st_mtime
    except OSError:
        return 0.0


def _unlink(path: Path) -> int:
    try:
        path.unlink()
        return 1
    except OSError as exc:  # locked on Windows: next run
        log.warning("could not remove %s: %s", path.name, exc)
        return 0


def init_app(app: Flask) -> PluginCache:
    cache = PluginCache(app.config["PLUGIN_CACHE_DIR"], app.config.get("PLUGIN_CACHE_MB") or 500)
    app.extensions[EXT_KEY] = cache
    return cache


def get_cache(app: Flask | None = None) -> PluginCache:
    return (app or current_app).extensions[EXT_KEY]


def prune_job() -> str:
    """Job plugin_cache_prune: see PluginCache.prune."""
    return get_cache().prune()
