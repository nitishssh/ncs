"""Files panel: thumbnail grid of the card, downloads with progress/cancel, and auto-import of new shots."""
from __future__ import annotations

import threading
import time
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from camctl import sync
from camctl.core import Cancelled

THUMB = QSize(160, 106)
DEFAULT_ROOT = Path.home() / "Pictures" / "R50V"


class ThumbLoader(QThread):
    loaded = Signal(str, bytes)

    def __init__(self, t, paths: list[str], busy=lambda: False):
        super().__init__()
        self.t, self.paths, self.stop, self.busy = t, paths, False, busy

    def run(self):
        # The camera serves only a few requests at once: yield to downloads, newest thumbnails first
        while self.paths and not self.stop:
            if self.busy():
                time.sleep(0.2)
                continue
            p = self.paths.pop(0)
            try:
                self.loaded.emit(p, self.t.thumbnail(p))
            except Exception:
                pass


class Downloader(QThread):
    progress = Signal(str, int, int, float)  # name, done, total, bytes/s
    done = Signal(str, str)                  # remote, local path
    failed = Signal(str, str)                # remote, message

    def __init__(self, t):
        super().__init__()
        self.t, self.queue, self.cancelled = t, [], False

    def add(self, remote: str, dest: Path):
        self.queue.append((remote, dest))

    def run(self):
        while self.queue and not self.cancelled:
            remote, dest = self.queue.pop(0)
            name, t0 = remote.rsplit("/", 1)[-1], time.time()
            try:
                out = self.t.download(remote, dest, cancel=lambda: self.cancelled,
                                      progress=lambda d, n: self.progress.emit(name, d, n, d / max(time.time() - t0, 1e-3)))
                self.done.emit(remote, str(out))
            except Cancelled:
                self.failed.emit(remote, "cancelled")
            except Exception as e:
                self.failed.emit(remote, str(e))


class FilesPanel(QWidget):
    message = Signal(str)
    synced = Signal(str)  # voice-sync results, emitted from the worker thread

    def __init__(self):
        super().__init__()
        self.t = None
        self.root = DEFAULT_ROOT
        self.thumbs: ThumbLoader | None = None
        self.dl: Downloader | None = None
        self.watcher = None  # new files now come from the main window's EventHub
        self.items: dict[str, QListWidgetItem] = {}
        self._got_movie = False
        self.synced.connect(self._sync_finished)

        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(THUMB)
        self.grid.setGridSize(QSize(THUMB.width() + 16, THUMB.height() + 34))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setUniformItemSizes(True)  # fixed cells: inserting at the top can't overlap neighbours
        self.grid.setSelectionMode(QListWidget.ExtendedSelection)
        self.grid.setMinimumHeight(170)
        self.grid.itemDoubleClicked.connect(lambda it: self.download([it.data(Qt.UserRole)]))

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh)
        self.get_btn = QPushButton("Download selected")
        self.get_btn.clicked.connect(lambda: self.download([i.data(Qt.UserRole) for i in self.grid.selectedItems()]))
        self.auto = QCheckBox("Auto-import new shots")
        self.auto.setToolTip("Download every new photo/movie as soon as the camera saves it")
        self.auto.toggled.connect(self.set_auto)
        self.jpeg_only = QCheckBox("JPEG only")
        self.jpeg_only.setToolTip("Auto-import skips RAW (.CR3) and movies; download them manually")
        self.folder_btn = QPushButton()
        self.folder_btn.clicked.connect(self.pick_folder)
        self.update_folder_label()

        self.bar = QProgressBar()
        self.bar.setVisible(False)
        self.bar.setTextVisible(False)
        self.bar.setMaximumHeight(6)
        self.bar_label = QLabel("")
        self.bar_label.setObjectName("cap")
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setVisible(False)
        self.cancel_btn.clicked.connect(self.cancel)

        tools = QHBoxLayout()
        self.sync_btn = QPushButton("Sync voice")
        self.sync_btn.setToolTip("Put the clean external-mic voice into every clip in the folder (<clip>_final.mp4)")
        self.sync_btn.clicked.connect(self.sync_voice)
        self.auto_sync = QCheckBox("Auto-sync voice")
        self.auto_sync.setToolTip("After movies download, merge the matching mic recording into <clip>_final.mp4")
        self.auto_sync.setChecked(True)
        self._sync_running = False
        self.import_btn = QPushButton("Import new")
        self.import_btn.setToolTip("Download every file on the card that isn't in the download folder yet")
        self.import_btn.clicked.connect(self.import_new)
        for w in (self.refresh_btn, self.import_btn, self.get_btn, self.auto, self.jpeg_only, self.sync_btn,
                  self.auto_sync):
            tools.addWidget(w)
        tools.addStretch()
        tools.addWidget(self.folder_btn)
        status = QHBoxLayout()
        status.addWidget(self.bar_label, 1)
        status.addWidget(self.cancel_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(tools)
        lay.addWidget(self.grid)
        lay.addWidget(self.bar)
        lay.addLayout(status)

    # destination: <root>/<YYYY-MM-DD>/
    def dest(self) -> Path:
        return self.root / date.today().isoformat()

    def update_folder_label(self):
        self.folder_btn.setText(f"📁 {self.root.name}/{date.today().isoformat()}")
        self.folder_btn.setToolTip(f"Downloads go to {self.dest()}")

    def pick_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Download folder", str(self.root))
        if path:
            self.root = Path(path)
            self.update_folder_label()

    # transport lifecycle
    def set_transport(self, t, caps: set[str]):
        self.stop_all()
        self.t = t
        self.caps = caps
        self.auto.setEnabled(t is not None and "events" in caps)
        if t is None:
            self.grid.clear()
            self.items.clear()
            return

    def stop_all(self):
        for th in (self.thumbs, self.watcher):
            if th:
                th.stop = True
                th.wait(200)
        if self.dl:
            self.dl.cancelled = True
            self.dl.wait(500)
        # keep references until the threads exit on their own (they stop at their next check)
        self._retired = [th for th in getattr(self, "_retired", []) if th.isRunning()]
        self._retired += [th for th in (self.thumbs, self.watcher, self.dl) if th and th.isRunning()]
        self.thumbs = self.watcher = self.dl = None

    def wait_retired(self, ms: int = 3000):
        """On app exit: give background threads time to notice they were stopped."""
        for th in getattr(self, "_retired", []):
            if isinstance(th, QThread):
                th.wait(ms)

    # grid
    def refresh(self):
        if not self.t:
            return
        try:
            paths = self.t.list_files()
        except Exception as e:
            self.message.emit(f"File list failed: {e}")
            return
        self.grid.clear()
        self.items.clear()
        for p in reversed(paths):
            self.add_item(p)
        self.load_thumbs(list(reversed(paths)))
        self.message.emit(f"{len(paths)} files on the card")

    def add_item(self, path: str, top: bool = False):
        name = path.rsplit("/", 1)[-1]
        it = QListWidgetItem(name)
        it.setData(Qt.UserRole, path)
        it.setToolTip(path.split("/contents/", 1)[-1])
        it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.items[path] = it
        if top:
            self.grid.insertItem(0, it)
            self.grid.doItemsLayout()  # IconMode doesn't re-flow on insert at the top
        else:
            self.grid.addItem(it)

    def load_thumbs(self, paths: list[str]):
        if not paths or "thumbnail" not in self.caps:
            return
        if self.thumbs and self.thumbs.isRunning():
            self.thumbs.paths = paths + self.thumbs.paths  # newest first
            return
        self.thumbs = ThumbLoader(self.t, paths, busy=lambda: bool(self.dl and self.dl.isRunning()))
        self.thumbs.loaded.connect(self.set_thumb)
        self.thumbs.start()

    def set_thumb(self, path: str, data: bytes):
        it = self.items.get(path)
        if it:
            pix = QPixmap.fromImage(QImage.fromData(data)).scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            it.setIcon(QIcon(pix))

    # downloads
    def download(self, remotes: list[str]):
        if not self.t or not remotes:
            return
        if not self.dl or not self.dl.isRunning():
            self.dl = Downloader(self.t)
            self.dl.progress.connect(self.on_progress)
            self.dl.done.connect(self.on_done)
            self.dl.failed.connect(self.on_failed)
            self.dl.finished.connect(self.on_queue_finished)
            for r in remotes:
                self.dl.add(r, self.dest())
            self.bar.setVisible(True)
            self.cancel_btn.setVisible(True)
            self.dl.start()
        else:
            for r in remotes:
                self.dl.add(r, self.dest())

    def import_new(self):
        """One click: queue everything on the card not already saved anywhere under the download root."""
        if not self.t:
            return
        try:
            paths = list(self.items) or self.t.list_files()
        except Exception as e:
            self.message.emit(f"File list failed: {e}")
            return
        have = {f.name for f in self.root.rglob("*") if f.is_file()} if self.root.exists() else set()
        jpeg = self.jpeg_only.isChecked()
        todo = [p for p in paths if p.rsplit("/", 1)[-1] not in have
                and (not jpeg or p.upper().endswith((".JPG", ".JPEG", ".HIF")))]
        if not todo:
            self.message.emit("Everything on the card is already imported")
            return
        self.download(sorted(todo))  # oldest first, in camera order
        self.message.emit(f"Importing {len(todo)} new files ({len(paths) - len(todo)} already saved)")

    def on_progress(self, name: str, done: int, total: int, rate: float):
        pct = int(done * 100 / total) if total else 0
        self.bar.setMaximum(100)
        self.bar.setValue(pct)
        left = (total - done) / rate if rate and total else 0
        queued = len(self.dl.queue) if self.dl else 0
        more = f"  ·  {queued} more queued" if queued else ""
        self.bar_label.setText(f"{name}  {done / 1e6:.1f} / {total / 1e6:.1f} MB  ·  {rate / 1e6:.1f} MB/s"
                               f"  ·  {left:.0f}s left{more}")

    def sync_voice(self):
        """Merge matching voice recordings into the clips of today's folder, in the background."""
        folder = self.dest()
        if self._sync_running or not folder.exists():
            return
        self._sync_running = True
        self.sync_btn.setEnabled(False)
        self.message.emit("Syncing voice into clips…")

        def work():
            try:
                results = sync.sync_folder(folder)
            except Exception as e:
                self.synced.emit(f"Voice sync failed: {e}")
                return
            if not results:
                self.synced.emit("Voice sync: no new clip has a matching mic recording")
                return
            parts = [f"{r.output.name} (offset {r.offset.seconds:+.2f}s)" if r.output else f"{r.clip.name}: {r.error}"
                     for r in results]
            self.synced.emit("Voice synced: " + "; ".join(parts))
        threading.Thread(target=work, daemon=True).start()

    def _sync_finished(self, msg: str):
        self._sync_running = False
        self.sync_btn.setEnabled(True)
        self.message.emit(msg)

    def on_done(self, remote: str, local: str):
        if local.lower().endswith(sync.VIDEO_EXT):
            self._got_movie = True
        it = self.items.get(remote)
        if it:
            it.setText("✓ " + remote.rsplit("/", 1)[-1])
        self.message.emit(f"Saved {local}")

    def on_failed(self, remote: str, msg: str):
        self.message.emit(f"{remote.rsplit('/', 1)[-1]}: {msg}")

    def on_queue_finished(self):
        if self.auto_sync.isChecked() and self._got_movie:
            self._got_movie = False
            self.sync_voice()
        self.bar.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.bar_label.setText("")

    def cancel(self):
        if self.dl:
            self.dl.cancelled = True
            self.dl.queue.clear()

    # auto-import
    def set_auto(self, on: bool):
        """Auto-import is driven by on_new_files(), which the main window's EventHub calls."""

    def on_new_files(self, paths: list[str]):
        if self.t is None:
            return
        if self.items:  # grid already loaded: show new files at the top
            for p in paths:
                if p not in self.items:
                    self.add_item(p, top=True)
            self.load_thumbs(paths)
        if not self.auto.isChecked():
            return
        wanted = [p for p in paths if not self.jpeg_only.isChecked() or p.upper().endswith((".JPG", ".JPEG", ".HIF"))]
        self.download(wanted)
