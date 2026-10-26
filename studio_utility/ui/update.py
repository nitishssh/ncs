"""Update prompt: check GitHub releases at launch (and from the menu), download with progress, then restart."""
from __future__ import annotations

import logging
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from studio_utility import __version__, updater

log = logging.getLogger(__name__)


class _Relay(QObject):
    checked = Signal(object, object)    # Release | None, error | None
    progress = Signal(int, int)
    downloaded = Signal(object, object)  # Path | None, error | None


class Updater:
    def __init__(self, window, prefs):
        self.win, self.prefs = window, prefs
        self.relay = _Relay(window)
        self.relay.checked.connect(self._checked)
        self.relay.progress.connect(self._progress)
        self.relay.downloaded.connect(self._downloaded)
        self.manual = False
        self.busy = False
        self.dialog: QProgressDialog | None = None

    def check(self, manual: bool = False):
        if self.busy:
            return
        self.busy, self.manual = True, manual

        def work():
            try:
                self.relay.checked.emit(updater.check(), None)
            except Exception as e:
                self.relay.checked.emit(None, e)
        threading.Thread(target=work, daemon=True, name="update-check").start()

    def _checked(self, rel, error):
        self.busy = False
        if error:
            log.info("Update check failed: %s", error)
            if self.manual:
                QMessageBox.warning(self.win, "Check for updates", f"Couldn't reach GitHub: {error}")
            return
        if not rel:
            if self.manual:
                QMessageBox.information(self.win, "Check for updates", f"Studio Utility {__version__} is up to date.")
            return
        if not self.manual and self.prefs.value("update_skip") == rel.version:
            return
        location = updater.installed_location()
        box = QMessageBox(self.win)
        box.setWindowTitle("Update available")
        box.setText(f"Studio Utility {rel.version} is available (you have {__version__}).")
        box.setDetailedText(rel.notes)
        if location:
            now = box.addButton("Update now", QMessageBox.AcceptRole)
        else:  # running from source or from the disk image: point to the download instead
            box.setInformativeText("This copy can't update itself (it isn't installed, or runs from source).")
            now = box.addButton("Open download page", QMessageBox.AcceptRole)
        box.addButton("Later", QMessageBox.RejectRole)
        skip = box.addButton("Skip this version", QMessageBox.DestructiveRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is skip:
            self.prefs.setValue("update_skip", rel.version)
        elif clicked is now:
            if location:
                self._download(rel, location)
            else:
                QDesktopServices.openUrl(QUrl(rel.page))

    def _download(self, rel, location):
        self.location = location
        self.dialog = QProgressDialog(f"Downloading Studio Utility {rel.version}…", None, 0, 100, self.win)
        self.dialog.setWindowModality(Qt.WindowModal)
        self.dialog.setMinimumDuration(0)
        self.dialog.setValue(0)
        dest = Path(tempfile.mkdtemp(prefix="studio-utility-download-"))

        def work():
            try:
                self.relay.downloaded.emit(updater.download(rel, dest, self.relay.progress.emit), None)
            except Exception as e:
                self.relay.downloaded.emit(None, e)
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _progress(self, done, total):
        if self.dialog and total:
            self.dialog.setValue(int(done * 100 / total))

    def _downloaded(self, package, error):
        if self.dialog:
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None
        if error:
            log.warning("Update download failed: %s", error)
            QMessageBox.warning(self.win, "Update", f"The update didn't download: {error}")
            return
        try:
            updater.install_and_restart(package, self.location)
        except Exception as e:
            log.warning("Update install failed: %s", e)
            QMessageBox.warning(self.win, "Update", f"The update couldn't be installed: {e}")
            return
        log.info("Installing update from %s; quitting so it can replace the app", package.name)
        QApplication.quit()
