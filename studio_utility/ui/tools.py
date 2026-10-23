"""Tools tab: interval/time-lapse, exposure bracketing and setting presets."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QInputDialog, QLabel,
                               QPushButton, QSpinBox, QWidget)

from studio_utility.labels import label
from studio_utility.sequences import Bracket, Interval, apply_preset, delete_preset, load_presets, save_preset


class _Bridge(QObject):
    """Sequence callbacks arrive on a worker thread; re-emit them on the Qt thread."""
    progress = Signal(str)
    done = Signal(str)


def _cap(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("cap")
    return lb


class ToolsTab(QWidget):
    message = Signal(str)

    def __init__(self, current_values):
        """current_values(): dict of the settings currently shown in the UI (for saving presets)."""
        super().__init__()
        self.t = None
        self.seq = None
        self.current_values = current_values
        self.bridge = _Bridge()
        self.bridge.progress.connect(self.status_text)
        self.bridge.done.connect(self.finished)

        # Interval / time-lapse
        self.every = QDoubleSpinBox(suffix=" s", decimals=1, minimum=1, maximum=3600, value=5)
        self.count = QSpinBox(minimum=0, maximum=9999, value=10, specialValueText="∞")
        self.clip = QDoubleSpinBox(suffix=" s", decimals=1, minimum=0, maximum=600, value=0,
                                   specialValueText="photo")
        self.clip.setToolTip("0 = take a photo each time; >0 = record a clip of this length (movie mode)")
        self.interval_btn = QPushButton("Start interval")
        self.interval_btn.clicked.connect(self.toggle_interval)

        # Bracketing
        self.frames = QComboBox()
        self.frames.addItems(["3", "5", "7"])
        self.step = QComboBox()
        for txt, thirds in (("⅓ stop", 1), ("⅔ stop", 2), ("1 stop", 3), ("2 stops", 6)):
            self.step.addItem(txt, thirds)
        self.step.setCurrentIndex(2)
        self.bracket_btn = QPushButton("Shoot bracket")
        self.bracket_btn.setToolTip("Uses exposure compensation: set the mode dial to P, Tv or Av")
        self.bracket_btn.clicked.connect(self.start_bracket)

        # Presets
        self.presets = QComboBox()
        self.presets.setMinimumWidth(160)
        self.apply_btn, self.save_btn, self.del_btn = QPushButton("Apply"), QPushButton("Save current…"), QPushButton("Delete")
        self.apply_btn.clicked.connect(self.apply)
        self.save_btn.clicked.connect(self.save)
        self.del_btn.clicked.connect(self.delete)
        self.reload_presets()

        self.state = _cap("")

        g = QGridLayout(self)
        g.setContentsMargins(0, 8, 0, 0)
        g.addWidget(_cap("Interval"), 0, 0)
        row = QHBoxLayout()
        for w in (_cap("every"), self.every, _cap("count"), self.count, _cap("clip"), self.clip, self.interval_btn):
            row.addWidget(w)
        row.addStretch()
        g.addLayout(row, 0, 1)
        g.addWidget(_cap("Bracket"), 1, 0)
        row = QHBoxLayout()
        for w in (_cap("frames"), self.frames, _cap("step"), self.step, self.bracket_btn):
            row.addWidget(w)
        row.addStretch()
        g.addLayout(row, 1, 1)
        g.addWidget(_cap("Presets"), 2, 0)
        row = QHBoxLayout()
        for w in (self.presets, self.apply_btn, self.save_btn, self.del_btn):
            row.addWidget(w)
        row.addStretch()
        g.addLayout(row, 2, 1)
        g.addWidget(self.state, 3, 0, 1, 2)
        g.setColumnStretch(1, 1)
        self.set_transport(None)

    def set_transport(self, t):
        if self.seq:
            self.seq.stop()
        self.t, self.seq = t, None
        for w in (self.interval_btn, self.bracket_btn, self.apply_btn, self.save_btn):
            w.setEnabled(t is not None)
        self.interval_btn.setText("Start interval")

    def status_text(self, msg: str):
        self.state.setText(msg)

    def finished(self, msg: str):
        self.seq = None
        self.interval_btn.setText("Start interval")
        self.bracket_btn.setEnabled(self.t is not None)
        self.state.setText(msg)
        self.message.emit(msg)

    def _run(self, seq):
        self.seq = seq
        seq.start(on_progress=lambda m, i, n: self.bridge.progress.emit(m), on_done=self.bridge.done.emit)

    def toggle_interval(self):
        if self.seq and self.seq.running:
            self.seq.stop()
            return
        self._run(Interval(self.t, every=self.every.value(), count=self.count.value(), clip=self.clip.value()))
        self.interval_btn.setText("Stop interval")
        self.bracket_btn.setEnabled(False)

    def start_bracket(self):
        if self.seq and self.seq.running:
            return
        self.bracket_btn.setEnabled(False)
        self._run(Bracket(self.t, frames=int(self.frames.currentText()), step_thirds=self.step.currentData()))

    # presets
    def reload_presets(self, select: str | None = None):
        self.presets.clear()
        names = sorted(load_presets())
        self.presets.addItems(names)
        if select in names:
            self.presets.setCurrentText(select)
        self.del_btn.setEnabled(bool(names))

    def save(self):
        name, ok = QInputDialog.getText(self, "Save preset", "Preset name:")
        if ok and name.strip():
            values = self.current_values()
            save_preset(name.strip(), values)
            self.reload_presets(name.strip())
            self.message.emit(f"Saved preset '{name.strip()}': " +
                              ", ".join(f"{k}={label(k, v)}" for k, v in values.items()))

    def apply(self):
        name = self.presets.currentText()
        values = load_presets().get(name)
        if not values or not self.t:
            return
        ok, failed = apply_preset(self.t, values)
        msg = f"Applied '{name}': {len(ok)} settings"
        if failed:
            msg += f"; not available now: {', '.join(failed)}"
        self.message.emit(msg)

    def delete(self):
        name = self.presets.currentText()
        if name:
            delete_preset(name)
            self.reload_presets()
