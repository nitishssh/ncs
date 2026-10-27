"""Audio editor window: waveform (raw and cleaned overlaid), drag to trim, A/B listening, re-clean, export."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from studio_utility import audio, editor, voice
from studio_utility.ui.audio_tab import _Bridge, _cap, tips_html

RAW, CLEAN, SEL, HEAD = QColor("#5c6bc0"), QColor("#43a047"), QColor(255, 255, 255, 28), QColor("#ffca28")


def fmt(s: float) -> str:
    return f"{int(s // 60):02d}:{s % 60:05.2f}"


class Waveform(QWidget):
    """Draws the take; drag selects the trim range, a click moves the playhead."""
    selected = Signal(float, float)   # seconds
    seek = Signal(float)

    def __init__(self):
        super().__init__()
        self.take: editor.Take | None = None
        self.show_clean = True
        self.head: float | None = None
        self._drag: float | None = None
        self._cache: dict = {}
        self.setMinimumHeight(160)
        self.setMouseTracking(True)

    def set_take(self, take):
        self.take, self._cache = take, {}
        self.update()

    def refresh(self):
        self._cache = {}
        self.update()

    def _x(self, s: float) -> float:
        return s / self.take.duration * self.width() if self.take and self.take.duration else 0

    def _s(self, x: float) -> float:
        return float(np.clip(x / max(1, self.width()), 0, 1)) * self.take.duration

    def _peaks(self, cleaned: bool):
        key = (cleaned, self.width(), id(self.take.version(cleaned)))
        if key not in self._cache:
            self._cache[key] = self.take.peaks(self.width(), cleaned)
        return self._cache[key]

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#161616"))
        if not self.take:
            p.setPen(QColor("#777"))
            p.drawText(self.rect(), Qt.AlignCenter, "Open a recording")
            return
        h, mid = self.height(), self.height() / 2
        layers = [(False, RAW)] + ([(True, CLEAN)] if self.show_clean and self.take.cleaned is not None else [])
        for cleaned, color in layers:
            p.setPen(QPen(color, 1))
            for x, (lo, hi) in enumerate(self._peaks(cleaned)):
                p.drawLine(QPointF(x + 0.5, mid - hi * mid * 0.95), QPointF(x + 0.5, mid - lo * mid * 0.95))
        p.setPen(QColor("#333"))
        p.drawLine(0, int(mid), self.width(), int(mid))
        if self.take.trimmed:
            a, b = self._x(self.take.start / self.take.rate), self._x(self.take.end / self.take.rate)
            shade = QColor(0, 0, 0, 150)
            p.fillRect(QRectF(0, 0, a, h), shade)          # what will be cut
            p.fillRect(QRectF(b, 0, self.width() - b, h), shade)
            p.fillRect(QRectF(a, 0, b - a, h), SEL)
        if self.head is not None:
            p.setPen(QPen(HEAD, 2))
            x = self._x(self.head)
            p.drawLine(QPointF(x, 0), QPointF(x, h))

    def mousePressEvent(self, e):
        if self.take:
            self._drag = self._s(e.position().x())

    def mouseMoveEvent(self, e):
        if self.take and self._drag is not None:
            self.take.set_trim(self._drag, self._s(e.position().x()))
            self.update()

    def mouseReleaseEvent(self, e):
        if not self.take or self._drag is None:
            return
        end = self._s(e.position().x())
        if abs(end - self._drag) * self.width() / self.take.duration < 4:  # a click: move the playhead
            self.seek.emit(end)
        else:
            self.take.set_trim(self._drag, end)
            self.selected.emit(self.take.start / self.take.rate, self.take.end / self.take.rate)
        self._drag = None
        self.update()

    def resizeEvent(self, e):
        self._cache = {}
        super().resizeEvent(e)


class EditorWindow(QDialog):
    """One take at a time. settings() and noise(path) come from the Audio tab, so a re-clean uses what's set there
    (noise(path): the learned room tone of the mic that recorded that file, or None)."""
    message = Signal(str)

    def __init__(self, settings, noise=lambda path: None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio editor")
        self.resize(1000, 460)
        self.settings = settings  # callable -> VoiceSettings
        self.noise = noise
        self.take: editor.Take | None = None
        self.play_from = 0.0
        self.play_started: float | None = None
        self.bridge = _Bridge()
        self.bridge.done.connect(self._cleaned)
        self.bridge.review.connect(self._reviewed)

        self.wave = Waveform()
        self.wave.selected.connect(lambda a, b: self.show_state())
        self.wave.seek.connect(self.seek)
        self.open_btn = QPushButton("Open…")
        self.open_btn.clicked.connect(self.choose)
        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setToolTip("Space")
        self.play_btn.clicked.connect(self.toggle_play)
        self.ab = QCheckBox("Hear cleaned (A/B)")
        self.ab.setToolTip("Switch between the raw recording and the cleaned version - also while playing (B key)")
        self.ab.toggled.connect(self.switch_ab)
        self.clean_btn = QPushButton("Re-clean with current settings")
        self.clean_btn.setToolTip("Denoise, EQ, compression and loudness from the Audio tab, applied to this take")
        self.clean_btn.clicked.connect(self.reclean)
        self.reset_btn = QPushButton("Clear trim")
        self.reset_btn.clicked.connect(self.clear_trim)
        self.export_raw = QPushButton("Export raw")
        self.export_raw.clicked.connect(lambda: self.export(False))
        self.export_clean = QPushButton("Export cleaned")
        self.export_clean.clicked.connect(lambda: self.export(True))
        self.info = _cap("")
        self.tips = QLabel("")
        self.tips.setWordWrap(True)
        self.tips.setTextFormat(Qt.RichText)

        top = QHBoxLayout()
        for w in (self.open_btn, self.play_btn, self.ab, self.clean_btn, self.reset_btn):
            top.addWidget(w)
        top.addStretch()
        top.addWidget(self.export_raw)
        top.addWidget(self.export_clean)
        lay = QVBoxLayout(self)
        lay.addLayout(top)
        lay.addWidget(self.wave, 1)
        lay.addWidget(self.info)
        lay.addWidget(self.tips)
        lay.addWidget(_cap("Drag across the waveform to trim · click to set where playback starts · "
                           "Space play/stop · B switch raw/cleaned"))

        QShortcut(QKeySequence(Qt.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key_B), self, self.ab.toggle)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.show_state()

    # ---- loading
    def choose(self):
        start = str(self.take.path.parent) if self.take else ""
        path, _ = QFileDialog.getOpenFileName(self, "Open recording", start, "Audio (*.wav *.flac *.aiff)")
        if path:
            self.load(Path(path))

    def load(self, path: Path):
        self.stop()
        try:
            self.take = editor.Take.load(path)
        except Exception as e:
            self.message.emit(f"Couldn't open {Path(path).name}: {e}")
            return
        self.play_from = 0.0
        self.wave.set_take(self.take)
        self.ab.setChecked(self.take.cleaned is not None)
        self.setWindowTitle(f"Audio editor - {self.take.raw_path().name}")
        self.show_state()
        self.review()

    # ---- state
    def show_state(self):
        t = self.take
        for w in (self.play_btn, self.ab, self.clean_btn, self.reset_btn, self.export_raw):
            w.setEnabled(t is not None)
        self.ab.setEnabled(t is not None and t.cleaned is not None)
        self.export_clean.setEnabled(t is not None and t.cleaned is not None)
        self.reset_btn.setEnabled(t is not None and t.trimmed)
        self.clean_btn.setEnabled(t is not None and voice.pb is not None)
        if not t:
            self.info.setText("")
            return
        sel = (t.end - t.start) / t.rate
        trim = f" · keeping {fmt(t.start / t.rate)} - {fmt(t.end / t.rate)} ({sel:.1f} s)" if t.trimmed else ""
        self.info.setText(f"{t.raw_path().name} · {fmt(t.duration)} · {t.rate // 1000} kHz · "
                          f"{t.audio.shape[1]} ch{trim} · {'cleaned version ready' if t.cleaned is not None else 'not cleaned yet'}")
        self.wave.update()

    def clear_trim(self):
        if self.take:
            self.take.reset_trim()
            self.show_state()

    # ---- playback
    @property
    def playing(self) -> bool:
        return self.play_started is not None

    def position(self) -> float:
        return self.play_from + (time.time() - self.play_started if self.playing else 0)

    def seek(self, s: float):
        was = self.playing
        self.stop()
        self.play_from = s
        self.wave.head = s
        self.wave.update()
        if was:
            self.play()

    def play(self):
        t = self.take
        if not t or audio.sd is None:
            return
        lo, hi = t.start / t.rate, t.end / t.rate
        if not lo <= self.play_from < hi - 0.05:
            self.play_from = lo
        x = t.version(self.ab.isChecked())[int(self.play_from * t.rate):t.end]
        try:
            audio.sd.play(x, t.rate)
        except Exception as e:
            self.message.emit(f"Can't play: {e}")
            return
        self.play_started = time.time()
        self.play_btn.setText("■ Stop")
        self.timer.start(30)

    def stop(self):
        if self.playing:
            self.play_from = self.position()
            audio.sd.stop()
        self.play_started = None
        self.timer.stop()
        self.play_btn.setText("▶ Play")

    def toggle_play(self):
        self.stop() if self.playing else self.play()

    def switch_ab(self):
        self.wave.show_clean = True
        if self.playing:  # A/B at the same moment, so the difference is all you hear
            self.seek(self.position())
        self.review()

    def tick(self):
        pos = self.position()
        if self.take and pos >= self.take.end / self.take.rate:
            self.stop()
            self.play_from = self.take.start / self.take.rate
            pos = self.play_from
        self.wave.head = pos
        self.wave.update()

    # ---- cleaning, suggestions, export
    def reclean(self):
        if not self.take:
            return
        self.stop()
        self.clean_btn.setEnabled(False)
        self.clean_btn.setText("Cleaning…")
        take, settings = self.take, self.settings()
        noise = self.noise(take.raw_path())

        def work():
            try:
                used = take.clean(settings, noise)
                self.bridge.done.emit(f"ok:{voice.ENGINES.get(used, used)}")
            except Exception as e:
                self.bridge.done.emit(f"error:{e}")
        threading.Thread(target=work, daemon=True).start()

    def _cleaned(self, state: str):
        self.clean_btn.setText("Re-clean with current settings")
        kind, _, text = state.partition(":")
        if kind == "error":
            self.message.emit(f"Clean failed: {text}")
        else:
            self.message.emit(f"Cleaned with {text} - press B to compare")
            self.wave.refresh()
            self.ab.setChecked(True)
        self.show_state()
        self.review()

    def review(self):
        if not self.take:
            return
        take, cleaned = self.take, self.ab.isChecked() and self.take.cleaned is not None

        def work():
            try:
                rep = take.report(cleaned)
                loud = f", {rep.lufs:.0f} LUFS" if rep.lufs is not None else ""
                self.bridge.review.emit(f"<b>{'Cleaned' if cleaned else 'Raw'}</b> (peak {rep.peak_db:.0f} dBFS{loud}, "
                                        f"voice {rep.snr_db:.0f} dB over room)<br>{tips_html(rep.tips)}")
            except Exception as e:
                self.bridge.review.emit(f"Couldn't check: {e}")
        threading.Thread(target=work, daemon=True).start()

    def _reviewed(self, html: str):
        self.tips.setText(html)

    def export(self, cleaned: bool):
        if not self.take:
            return
        try:
            path = self.take.export(cleaned)
        except Exception as e:
            self.message.emit(f"Export failed: {e}")
            return
        self.message.emit(f"Exported {path.name}")
        self.info.setText(self.info.text() + f" · saved {path.name}")

    def closeEvent(self, e):
        self.stop()
        super().closeEvent(e)
