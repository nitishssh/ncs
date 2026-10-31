"""Lighting tab: place key/fill/back lights, preview the subject, get (and send) matching exposure + WB."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QHBoxLayout, QLabel,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

from studio_utility.lighting import Scene, WB_PRESETS, render, suggest
from studio_utility.labels import label

APERTURES = [1.8, 2.8, 4.0, 5.6, 8.0, 11.0]
FPS = [("Photo", None), ("24p", 23.98), ("30p", 29.97), ("60p", 59.94), ("120p", 119.88)]


def _cap(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setObjectName("cap")
    return lb


class LightingTab(QWidget):
    message = Signal(str)

    def __init__(self, choices, apply):
        """choices(key) -> the camera's values for a setting ([] when unknown);
        apply(settings) -> sends them to the camera, returns the keys it could set."""
        super().__init__()
        self.choices, self.apply_fn = choices, apply
        self.scene = Scene()
        self.result = None
        self.rows = []
        grid = QGridLayout()
        grid.setContentsMargins(0, 8, 0, 0)
        for col, text in enumerate(("", "Lux @1m", "Dist. m", "Angle °", "Height °", "Kelvin")):
            grid.addWidget(_cap(text), 0, col)
        for row, light in enumerate(self.scene.lights, 1):
            on = QCheckBox(light.name)
            on.setChecked(light.on)
            spins = [QSpinBox(minimum=0, maximum=50000, singleStep=100, value=int(light.lux_1m)),
                     QDoubleSpinBox(minimum=0.3, maximum=10, singleStep=0.1, decimals=1, value=light.distance),
                     QSpinBox(minimum=-180, maximum=180, singleStep=15, value=int(light.azimuth)),
                     QSpinBox(minimum=-30, maximum=90, singleStep=15, value=int(light.elevation)),
                     QSpinBox(minimum=2000, maximum=10000, singleStep=100, value=light.kelvin)]
            on.toggled.connect(self.update)
            grid.addWidget(on, row, 0)
            for col, s in enumerate(spins, 1):
                s.valueChanged.connect(self.update)
                grid.addWidget(s, row, col)
            self.rows.append((on, *spins))
        n = len(self.scene.lights) + 1
        grid.addWidget(_cap("Ambient"), n, 0)
        self.amb_lux = QSpinBox(minimum=0, maximum=100000, singleStep=10, value=int(self.scene.ambient_lux))
        self.amb_k = QSpinBox(minimum=2000, maximum=10000, singleStep=100, value=self.scene.ambient_kelvin)
        for s, col in ((self.amb_lux, 1), (self.amb_k, 5)):
            s.valueChanged.connect(self.update)
            grid.addWidget(s, n, col)

        self.fps = QComboBox()
        for text, fps in FPS:
            self.fps.addItem(text, fps)
        self.fps.setToolTip("Movie frame rates lock the shutter to 180° (1/2×fps)")
        self.aperture = QComboBox()
        for f in APERTURES:
            self.aperture.addItem(f"f/{f:g}", f)
        self.aperture.setCurrentIndex(2)
        for b in (self.fps, self.aperture):
            b.currentIndexChanged.connect(self.update)
        opts = QHBoxLayout()
        opts.addWidget(_cap("Shoot"))
        opts.addWidget(self.fps)
        opts.addWidget(_cap("Aperture"))
        opts.addWidget(self.aperture)
        opts.addStretch()

        self.readout = QLabel()
        self.readout.setObjectName("pill")
        self.notes = _cap("")
        self.notes.setWordWrap(True)
        self.apply_btn = QPushButton("Send to camera")
        self.apply_btn.setToolTip("Sets shutter, aperture, ISO and WB. To keep it as C3, save it on the camera: "
                                  "Menu → Custom shooting mode (C1–C3) → Register settings")
        self.apply_btn.clicked.connect(self.send)
        self.preview = QLabel()
        self.preview.setFixedSize(200, 200)
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setObjectName("preview")

        left = QVBoxLayout()
        left.addLayout(grid)
        left.addLayout(opts)
        left.addWidget(self.readout)
        left.addWidget(self.notes)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.apply_btn)
        btn_row.addStretch()
        left.addLayout(btn_row)
        left.addStretch()
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(left, 1)
        root.addWidget(self.preview, 0, Qt.AlignTop)
        self.set_connected(False)
        self.update()

    def set_connected(self, on: bool):
        self.apply_btn.setEnabled(on)

    def update(self, *_):
        for light, (on, lux, dist, az, el, k) in zip(self.scene.lights, self.rows):
            light.on, light.lux_1m, light.distance = on.isChecked(), lux.value(), dist.value()
            light.azimuth, light.elevation, light.kelvin = az.value(), el.value(), k.value()
        self.scene.ambient_lux, self.scene.ambient_kelvin = self.amb_lux.value(), self.amb_k.value()
        keys = ("tv", "av", "iso", "wb", "color_temp")
        cam = {k: [c for c in self.choices(k) if c] for k in keys}
        s = self.result = suggest(self.scene, self.aperture.currentData(), self.fps.currentData(), cam)
        wb = label("wb", s.wb) if s.wb else "—"
        if s.color_temp:
            wb = f"{s.color_temp}K"
        self.readout.setText(f"{self.scene.subject_lux():.0f} lux  ·  EV {self.scene.ev100():.1f}  ·  "
                             f"{s.tv}  {s.av}  ISO {s.iso}  ·  WB {wb} (scene {self.scene.kelvin()}K)"
                             + (f"  ·  {s.error_stops:+.1f} EV" if abs(s.error_stops) >= 0.2 else ""))
        self.notes.setText("\n".join(s.notes))
        wb_k = int(s.color_temp) if s.color_temp else WB_PRESETS.get(s.wb, self.scene.kelvin())
        img = render(self.scene, wb_k, s.error_stops, 200)
        q = QImage(img.data, img.shape[1], img.shape[0], img.strides[0], QImage.Format_RGB888)
        self.preview.setPixmap(QPixmap.fromImage(q.copy()))

    def showEvent(self, e):  # the camera's value lists may have loaded since the last update
        self.update()
        super().showEvent(e)

    def send(self):
        self.update()
        done = self.apply_fn(self.result.settings())
        if done:
            self.message.emit(f"Sent {', '.join(done)}. To keep it, register it as C3 on the camera.")
        else:
            self.message.emit("The camera didn't accept any of these settings over this connection")
