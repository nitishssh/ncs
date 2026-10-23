"""The single consumer of the camera's event queue; fans events out to the UI as a Qt signal."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal


class EventHub(QObject):
    """Long-polls transport.events() in a daemon thread (a camera long-poll can't be interrupted
    and must not keep the app from exiting). Emits normalized event dicts on the Qt thread."""
    event = Signal(dict)

    def __init__(self, t):
        super().__init__()
        self.t, self.stop = t, False
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def _run(self):
        while not self.stop:
            try:
                ev = self.t.events(timeout=10)
            except Exception:
                time.sleep(2)  # camera busy or briefly unreachable; keep listening
                continue
            if ev and not self.stop:
                self.event.emit(ev)
