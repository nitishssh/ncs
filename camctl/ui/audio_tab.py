"""Audio tab: two mic slots (e.g. DJI Mic receiver + Kreo Sonik) with live meters, recording with the camera."""
from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton,
                               QVBoxLayout, QWidget)

from camctl import audio

FLOOR = -60  # meter range: -60 .. 0 dBFS

METER = ("QProgressBar {{ background:#1a1a1a; border:1px solid #2a2a2a; border-radius:3px; height:10px; }}"
         "QProgressBar::chunk {{ border-radius:2px; background:{}; }}")
GREEN, YELLOW, RED = METER.format("#43a047"), METER.format("#f9a825"), METER.format("#e53935")


def meter_style(peak_db: float) -> str:
    """Solid colour by level: green < -12 dBFS (good), yellow up to -3 (hot), red above (about to clip)."""
    return GREEN if peak_db < -12 else YELLOW if peak_db < -3 else RED


def _cap(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("cap")
    return lb


class MicSlot(QWidget):
    """Device picker + one meter per channel + clip light."""
    message = Signal(str)

    def __init__(self, title: str, prefer: str):
        super().__init__()
        self.title, self.prefer, self.mic = title, prefer, None
        self.device = QComboBox()
        self.device.setMinimumWidth(220)
        self.device.currentIndexChanged.connect(self.open_device)
        self.meters = [QProgressBar() for _ in range(2)]
        self.db = [_cap("") for _ in range(2)]
        for m in self.meters:
            m.setRange(FLOOR, 0)
            m.setValue(FLOOR)
            m.setTextVisible(False)
            m.setStyleSheet(GREEN)
        self.clip = QPushButton("CLIP")
        self.clip.setToolTip("Lit when the signal hit 0 dBFS (distortion). Click to reset.")
        self.clip.setMinimumWidth(64)
        self.clip.clicked.connect(lambda: self.mic and self.mic.clear_clip())

        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(_cap(title), 0, 0)
        g.addWidget(self.device, 0, 1, 1, 2)
        g.addWidget(self.clip, 0, 3)
        for i, (m, d) in enumerate(zip(self.meters, self.db)):
            g.addWidget(_cap("L" if i == 0 else "R"), 1 + i, 0, Qt.AlignRight)
            g.addWidget(m, 1 + i, 1, 1, 2)
            g.addWidget(d, 1 + i, 3)
        g.setColumnStretch(1, 1)

    def fill(self, saved: str | None):
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("Off", None)
        for d in audio.input_devices():
            self.device.addItem(d.label, d.name)
        want = saved if saved is not None else next(
            (self.device.itemData(i) for i in range(self.device.count())
             if self.device.itemData(i) and self.prefer.lower() in self.device.itemData(i).lower()), None)
        i = self.device.findData(want)
        self.device.setCurrentIndex(i if i >= 0 else 0)
        self.device.blockSignals(False)
        self.open_device()

    def open_device(self):
        if self.mic:
            self.mic.close()
            self.mic = None
        name = self.device.currentData()
        if name:
            try:
                self.mic = audio.MicInput(name)
            except Exception as e:
                self.message.emit(f"{self.title}: can't open {name}: {e}")
                self.device.setCurrentIndex(0)
        for m, d in zip(self.meters, self.db):
            m.setValue(FLOOR)
            d.setText("")

    def tick(self):
        if not self.mic:
            return
        levels = self.mic.levels()
        silent = all(p <= -119 for _, p in levels)
        for (rms, peak), m, d in zip(levels, self.meters, self.db):
            m.setValue(int(max(FLOOR, min(0, peak))))
            style = meter_style(peak)
            if m.styleSheet() != style:
                m.setStyleSheet(style)
            d.setText("no signal" if silent else f"{peak:5.0f} dB")
        self.clip.setStyleSheet("background:#c62828;color:white;" if self.mic.clipped else "")

    def close(self):
        if self.mic:
            self.mic.close()
            self.mic = None


class AudioTab(QWidget):
    message = Signal(str)

    def __init__(self, folder):
        """folder(): directory for the WAV files (the Files panel's dated import folder)."""
        super().__init__()
        self.folder = folder
        self.prefs = QSettings("camctl", "camctl")
        self.slots = [MicSlot("Mic A", "Wireless Mic Rx"), MicSlot("Mic B", "Kreo Sonik")]
        for s in self.slots:
            s.message.connect(self.message)
            s.device.currentIndexChanged.connect(self.save_choice)
        self.with_camera = QCheckBox("Record with camera")
        self.with_camera.setToolTip("Start/stop WAV recording on both mics when the camera's Rec starts/stops")
        self.with_camera.setChecked(self.prefs.value("audio_with_camera", True, type=bool))
        self.with_camera.toggled.connect(lambda on: self.prefs.setValue("audio_with_camera", on))
        self.rec_btn = QPushButton("● Record audio")
        self.rec_btn.clicked.connect(lambda: self.record(not self.recording))
        self.rescan = QPushButton("Rescan")
        self.rescan.setToolTip("Look for newly plugged-in receivers")
        self.rescan.clicked.connect(self.load_devices)
        self.state = _cap("Unavailable: install sounddevice + soundfile" if audio.sd is None else "")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        for s in self.slots:
            row.addWidget(s, 1)
        lay.addLayout(row)
        row = QHBoxLayout()
        for w in (self.with_camera, self.rec_btn, self.rescan):
            row.addWidget(w)
        row.addStretch()
        row.addWidget(self.state)
        lay.addLayout(row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        if audio.sd is not None:
            self.load_devices()
            self.timer.start(50)
        else:
            for w in (self.rec_btn, self.rescan, self.with_camera):
                w.setEnabled(False)

    def load_devices(self):
        for i, s in enumerate(self.slots):
            saved = self.prefs.value(f"audio_slot{i}", None)
            s.fill(saved)

    def save_choice(self):
        for i, s in enumerate(self.slots):
            self.prefs.setValue(f"audio_slot{i}", s.device.currentData() or "")

    @property
    def recording(self) -> bool:
        return any(s.mic and s.mic.recording for s in self.slots)

    def record(self, start: bool):
        if start:
            stamp = time.strftime("%Y-%m-%d_%H%M%S")
            paths = []
            for s in self.slots:
                if s.mic:
                    brand = audio.KNOWN.get(s.mic.device, s.mic.device).replace(" ", "")
                    paths.append(s.mic.start_recording(Path(self.folder()) / f"{stamp}_{s.title.replace(' ', '')}_{brand}.wav"))
            if not paths:
                self.message.emit("No mic selected in the Audio tab")
                return
            self.message.emit("Recording audio: " + ", ".join(p.name for p in paths))
        else:
            saved = [s.mic.stop_recording() for s in self.slots if s.mic and s.mic.recording]
            if saved:
                self.message.emit("Saved audio: " + ", ".join(str(p) for p in saved))
        self.rec_btn.setText("■ Stop audio" if self.recording else "● Record audio")

    def camera_record(self, start: bool):
        """Called by the main window when the camera's Rec starts/stops."""
        if self.with_camera.isChecked() and audio.sd is not None:
            self.record(start)

    def tick(self):
        for s in self.slots:
            s.tick()
        if self.recording:
            t0 = min(s.mic.started_at for s in self.slots if s.mic and s.mic.recording)
            secs = int(time.time() - t0)
            self.state.setText(f"● REC audio {secs // 60:02d}:{secs % 60:02d}")
        elif not self.state.text().startswith("Unavailable"):
            self.state.setText("")

    def shutdown(self):
        if self.recording:
            self.record(False)
        self.timer.stop()
        for s in self.slots:
            s.close()
