"""Guided connection setup and local prerequisite checks."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QLabel, QLineEdit, QPushButton, QVBoxLayout)

from studio_utility.diagnostics import write_support_report

KINDS = {
    "hub": ("Studio Hub (recommended)", "Paste the pairing link shown by the computer running Studio Hub."),
    "wifi": ("Camera Control API", "Enter the camera or relay address, for example 10.11.0.2:8090."),
    "usb-edsdk": ("Canon USB (EDSDK)", "Connect USB, close Canon EOS Utility, and install Canon EDSDK."),
    "usb-gphoto": ("USB fallback (gPhoto2)", "Connect USB and close macOS Photos or PTPCamera."),
    "ble": ("Bluetooth remote", "Enter the camera Bluetooth address. Shooting support is experimental."),
    "video": ("Webcam / capture device", "Enter the video-device number, normally 0."),
    "mock": ("Demo camera", "Use the built-in camera simulator without hardware."),
}


def diagnose_connection(kind: str, value: str) -> list[tuple[str, str]]:
    """Return actionable local checks without contacting or changing the camera."""
    checks: list[tuple[str, str]] = []
    if kind == "hub":
        checks.append(("ok" if value.startswith(("http://", "https://")) else "error",
                       "Pairing link detected" if value.startswith(("http://", "https://")) else "Paste the complete Hub pairing link"))
    elif kind == "wifi":
        checks.append(("ok" if value else "error", "Address entered" if value else "Enter the camera or relay address"))
    elif kind == "usb-edsdk":
        configured = os.environ.get("STUDIO_UTILITY_EDSDK")
        found = bool(configured and Path(configured).exists())
        checks.append(("ok" if found else "error", "Canon EDSDK found" if found else "Set STUDIO_UTILITY_EDSDK to the Canon EDSDK library"))
    elif kind == "usb-gphoto":
        found = shutil.which("gphoto2") is not None
        checks.append(("ok" if found else "error", "gPhoto2 found" if found else "Install gPhoto2 first"))
    elif kind == "video":
        checks.append(("ok" if value.isdigit() else "error", "Video-device number is valid" if value.isdigit() else "Enter a numeric device number"))
    elif kind == "ble":
        checks.append(("ok" if value else "warning", "Bluetooth address entered" if value else "Scan for the camera address first"))
    else:
        checks.append(("ok", "Demo mode is ready"))
    return checks


class ConnectionDialog(QDialog):
    def __init__(self, kind: str, value: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Camera connection setup")
        self.setMinimumWidth(520)
        self.kind = QComboBox()
        for key, (name, _help) in KINDS.items():
            self.kind.addItem(name, key)
        self.kind.setCurrentIndex(max(0, self.kind.findData(kind)))
        self.value = QLineEdit(value)
        self.help = QLabel()
        self.help.setWordWrap(True)
        self.results = QLabel()
        self.results.setWordWrap(True)
        self.kind.currentIndexChanged.connect(self.refresh)
        self.value.textChanged.connect(self.refresh)

        form = QFormLayout()
        form.addRow("Connection", self.kind)
        form.addRow("Address / device", self.value)
        save = QPushButton("Save support report…")
        save.clicked.connect(self.save_report)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("Use connection")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.help)
        layout.addWidget(self.results)
        layout.addWidget(save)
        layout.addWidget(buttons)
        self.refresh()

    def selected(self) -> tuple[str, str]:
        return self.kind.currentData(), self.value.text().strip()

    def refresh(self):
        kind, value = self.selected()
        self.help.setText(KINDS[kind][1])
        icons = {"ok": "✓", "warning": "⚠", "error": "✕"}
        self.results.setText("\n".join(f"{icons[level]} {message}" for level, message in diagnose_connection(kind, value)))

    def save_report(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save support report", "studio-utility-support.txt", "Text (*.txt)")
        if path:
            write_support_report(path)
