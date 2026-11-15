"""Background executor for plugin fetches: 2 daemon threads per process.

Request threads never wait on upstream servers: they submit work here and wait at
most a moment (see cache.py). The queue holds at most 32 tasks; overflow is dropped
and retried on the next read. Threads start on the first submit, so CLI commands such
as `flask db upgrade` start none. With PLUGIN_FETCH_SYNC (tests) tasks run inline.
"""
import logging
import queue
import threading

from flask import Flask, current_app

log = logging.getLogger(__name__)

EXT_KEY = "aisignx_plugin_worker"
MAX_WORKERS = 2
MAX_QUEUE = 32
_STOP = object()


class Task:
    """A submitted call: wait() for it, then read result or error."""

    def __init__(self, fn, args):
        self.fn, self.args = fn, args
        self.done = threading.Event()
        self.result = None
        self.error: BaseException | None = None

    def run(self) -> None:
        try:
            self.result = self.fn(*self.args)
        except BaseException as exc:  # recorded for the waiter; never kills the worker
            self.error = exc
        finally:
            self.done.set()

    def wait(self, timeout: float | None = None) -> bool:
        return self.done.wait(timeout)


class Worker:
    def __init__(self, app: Flask, sync: bool = False, workers: int = MAX_WORKERS):
        self.app = app
        self.sync = sync
        self.workers = workers
        self.queue: queue.Queue = queue.Queue(maxsize=MAX_QUEUE)
        self.threads: list[threading.Thread] = []
        self._lock = threading.Lock()

    def _ensure_threads(self) -> None:
        with self._lock:
            self.threads = [t for t in self.threads if t.is_alive()]
            while len(self.threads) < self.workers:
                thread = threading.Thread(target=self._loop, daemon=True,
                                          name=f"aisignx-plugins-{len(self.threads) + 1}")
                thread.start()
                self.threads.append(thread)

    def submit(self, fn, *args) -> Task | None:
        """Queue fn(*args) to run inside an app context. None when the queue is full."""
        task = Task(fn, args)
        return task if self.submit_task(task) else None

    def submit_task(self, task: Task) -> bool:
        """Queue a prepared Task (sync mode: run it now). False when the queue is full."""
        if self.sync:
            self._run(task)
            return True
        self._ensure_threads()
        try:
            self.queue.put_nowait(task)
        except queue.Full:
            log.warning("plugin worker queue is full; a fetch was dropped")
            return False
        return True

    def _run(self, task: Task) -> None:
        with self.app.app_context():
            task.run()
            if task.error is not None and not isinstance(task.error, Exception):
                raise task.error
            if task.error is not None:
                log.debug("plugin task failed: %r", task.error)

    def _loop(self) -> None:
        while True:
            task = self.queue.get()
            if task is _STOP:
                return
            try:
                self._run(task)
            except BaseException:  # keep the thread alive whatever happens
                log.exception("plugin worker task crashed")

    def stop(self) -> None:
        """Ask every thread to finish (tests)."""
        for _ in self.threads:
            try:
                self.queue.put_nowait(_STOP)
            except queue.Full:
                break


def init_app(app: Flask) -> Worker:
    worker = Worker(app, sync=bool(app.config.get("PLUGIN_FETCH_SYNC")))
    app.extensions[EXT_KEY] = worker
    return worker


def get_worker(app: Flask | None = None) -> Worker:
    return (app or current_app).extensions[EXT_KEY]
