"""Small Qt bridge for camera calls that must not block the UI thread."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from PySide6.QtCore import QObject, Signal


class CameraExecutor(QObject):
    """Run bounded camera I/O and deliver callbacks on Qt's owning thread.

    A key may have only one pending call. This is especially important for live
    view and status polling: a slow camera drops refreshes instead of building an
    unbounded queue that makes controls appear frozen.
    """

    completed = Signal(object, object)
    failed = Signal(object, object)

    def __init__(self, workers: int = 2, parent: QObject | None = None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="camera-ui")
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._closed = False
        self.completed.connect(lambda callback, value: callback(value))
        self.failed.connect(lambda callback, error: callback(error))

    def submit(self, key: str, fn: Callable, done: Callable, failed: Callable) -> bool:
        with self._lock:
            if self._closed or key in self._pending:
                return False
            self._pending.add(key)
        future = self._pool.submit(fn)

        def finished(result):
            with self._lock:
                self._pending.discard(key)
                closed = self._closed
            if closed:
                return
            try:
                value = result.result()
            except Exception as error:
                self.failed.emit(failed, error)
            else:
                self.completed.emit(done, value)

        future.add_done_callback(finished)
        return True

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._pool.shutdown(wait=False, cancel_futures=True)
