"""Files panel: thumbnail grid of the card, downloads with progress/cancel, and auto-import of new shots."""
from __future__ import annotations

import threading
import re
import time
from datetime import date
from pathlib import Path

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QProgressBar, QPushButton, QVBoxLayout, QWidget)

from studio_utility import sync
from studio_utility.core import Cancelled
from studio_utility.imports import ImportJournal

THUMB = QSize(160, 106)
FIRST_THUMBS = 30  # thumbnails fetched up front (the newest); the rest as they scroll into view
THUMB_GAP_S = 0.03  # pause between fetches: over USB on macOS each one runs on the UI thread
DEFAULT_ROOT = Path.home() / "Pictures" / "R50V"


_CANON_TIME = re.compile(r"C\d{3}[A-Z](\d{6})_(\d{6})")  # A_0001C051A261027_0159138U_CANON.MP4 -> 261027 015913


def newest_first(paths: list[str], times: dict[str, float] | None = None) -> list[str]:
    """Card files, newest first. Transports list folder by folder (all photos, then all movies), so their order
    put a clip taken a minute ago below every photo. Sort by the capture time the transport reports, else the time
    in Canon's movie names, else the file number (IMG_0146 after IMG_0145)."""
    times = times or {}

    def key(p: str):
        name = p.rsplit("/", 1)[-1]
        if p in times:
            return (times[p], name)
        m = _CANON_TIME.search(name)
        if m:
            return (time.mktime(time.strptime(m[1] + m[2], "%y%m%d%H%M%S")), name)
        return (0.0, name)
    return sorted(paths, key=key, reverse=True)


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
            time.sleep(THUMB_GAP_S)  # 259 back-to-back fetches left the window unresponsive (2026-10-27)


class Downloader(QThread):
    started = Signal(str, str)                # remote, destination
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
            self.started.emit(remote, str(dest))
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
        self.grid.setMinimumHeight(THUMB.height() + 40)  # one full row of thumbnails with names
        self.grid.itemDoubleClicked.connect(lambda it: self.download([it.data(Qt.UserRole)]))
        self.scroll_settle = QTimer(self, singleShot=True, interval=120)  # one thumbnail check per scroll, not per pixel
        self.scroll_settle.timeout.connect(self.want_visible_thumbs)
        self.grid.verticalScrollBar().valueChanged.connect(lambda _v: self.scroll_settle.start())

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
        self.resume_btn = QPushButton("Resume interrupted")
        self.resume_btn.setToolTip("Retry transfers saved from an earlier session")
        self.resume_btn.clicked.connect(self.resume_interrupted)
        self.resume_btn.setVisible(False)
        for w in (self.refresh_btn, self.import_btn, self.get_btn, self.auto, self.jpeg_only, self.sync_btn,
                  self.auto_sync, self.resume_btn):
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
            self.update_resume_button()

    @property
    def journal(self) -> ImportJournal:
        return ImportJournal(self.root)

    def update_resume_button(self):
        count = len(self.journal.pending())
        self.resume_btn.setText(f"Resume interrupted ({count})")
        self.resume_btn.setVisible(count > 0)

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
        self.update_resume_button()

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
        newest = newest_first(paths, getattr(self.t, "file_times", None))
        for p in newest:
            self.add_item(p)
        self.load_thumbs(newest[:FIRST_THUMBS])
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

    def want_visible_thumbs(self):
        """Queue thumbnails for items scrolled into view that don't have one yet."""
        view = self.grid.viewport().rect()
        missing = [p for p, it in self.items.items()
                   if it.icon().isNull() and self.grid.visualItemRect(it).intersects(view)]
        if missing:
            self.load_thumbs([p for p in missing if not (self.thumbs and p in self.thumbs.paths)])

    def set_thumb(self, path: str, data: bytes):
        it = self.items.get(path)
        if it:
            pix = QPixmap.fromImage(QImage.fromData(data)).scaled(THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            it.setIcon(QIcon(pix))

    # downloads
    def download(self, remotes: list[str], destinations: dict[str, Path] | None = None):
        if not self.t or not remotes:
            return
        destinations = destinations or {}
        for remote in remotes:
            destination = destinations.get(remote, self.dest())
            self.journal.update(remote, destination, "queued")
        if not self.dl or not self.dl.isRunning():
            self.dl = Downloader(self.t)
            self.dl.started.connect(self.on_download_started)
            self.dl.progress.connect(self.on_progress)
            self.dl.done.connect(self.on_done)
            self.dl.failed.connect(self.on_failed)
            self.dl.finished.connect(self.on_queue_finished)
            for r in remotes:
                self.dl.add(r, destinations.get(r, self.dest()))
            self.bar.setVisible(True)
            self.cancel_btn.setVisible(True)
            self.dl.start()
        else:
            for r in remotes:
                self.dl.add(r, destinations.get(r, self.dest()))

    def resume_interrupted(self):
        pending = self.journal.pending()
        if not pending:
            self.update_resume_button()
            return
        destinations = {remote: destination for remote, destination in pending}
        self.download(list(destinations), destinations)
        self.message.emit(f"Resuming {len(pending)} interrupted transfer(s)")

    def on_download_started(self, remote: str, destination: str):
        self.journal.update(remote, Path(destination), "downloading")

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
            parts = [(f"{r.output.name} (offset {r.offset.seconds:+.2f}s)" + (f" ⚠ {r.warning}" if r.warning else ""))
                     if r.output else f"{r.clip.name}: {r.error}" for r in results]
            self.synced.emit("Voice synced: " + "; ".join(parts))
        threading.Thread(target=work, daemon=True).start()

    def _sync_finished(self, msg: str):
        self._sync_running = False
        self.sync_btn.setEnabled(True)
        self.message.emit(msg)

    def on_done(self, remote: str, local: str):
        self.journal.update(remote, Path(local).parent, "completed")
        if local.lower().endswith(sync.VIDEO_EXT):
            self._got_movie = True
            self.check_clip_sound(Path(local))
        it = self.items.get(remote)
        if it:
            it.setText("✓ " + remote.rsplit("/", 1)[-1])
        self.message.emit(f"Saved {local}")

    def check_clip_sound(self, clip: Path):
        """Warn right after download when the camera recorded no (or almost no) sound."""
        def work():
            try:
                problem = sync.clip_sound_problem(clip)
            except Exception:
                return  # an unreadable clip is reported elsewhere; never block the import on this check
            if problem:
                self.message.emit(f"⚠ {clip.name}: {problem}")
        threading.Thread(target=work, daemon=True).start()

    def on_failed(self, remote: str, msg: str):
        current = dict(self.journal.pending()).get(remote, self.dest())
        self.journal.update(remote, current, "cancelled" if msg == "cancelled" else "failed", msg)
        self.update_resume_button()
        self.message.emit(f"{remote.rsplit('/', 1)[-1]}: {msg}")

    def on_queue_finished(self):
        if self.auto_sync.isChecked() and self._got_movie:
            self._got_movie = False
            self.sync_voice()
        self.bar.setVisible(False)
        self.cancel_btn.setVisible(False)
        self.bar_label.setText("")
        self.update_resume_button()

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
