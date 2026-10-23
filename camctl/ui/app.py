"""Creator Panel - minimal PySide6 control UI, same code on macOS and Windows."""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMainWindow, QPushButton, QSlider, QTabWidget,
                               QVBoxLayout, QWidget)

import cv2
import numpy as np

from camctl import transports
from camctl.lut import apply_lut, load_cube
from camctl.core import NotSupported
from camctl import overlays
from camctl.labels import label
from camctl.ui.files import FilesPanel
from camctl.ui.events import EventHub
from camctl.ui.tools import ToolsTab

PHOTO = ["mode", "tv", "av", "iso", "exposure", "wb", "af_method"]
MOVIE = ["movie_mode", "movie_size", "movie_format", "movie_af"]
COLOR = ["canon_log", "picture_style", "log_assist", "hdr_pq"]
LABELS = {"exposure": "Exp. comp", "af_method": "AF method", "mode": "Mode", "tv": "Shutter", "av": "Aperture", "iso": "ISO", "wb": "WB",
          "movie_mode": "Movie mode", "movie_size": "Size / fps", "movie_format": "Format",
          "movie_af": "Movie AF", "canon_log": "Canon Log", "picture_style": "Picture Style",
          "log_assist": "View Assist", "hdr_pq": "HDR PQ"}
DEFAULT_ARG = {"mock": "", "wifi": "10.11.0.2:8090", "ble": "", "usb-edsdk": "", "usb-gphoto": "", "video": "0"}

STYLE = """
QWidget { background:#121212; color:#e8e8e8; font-size:13px; }
QComboBox, QLineEdit { background:#1e1e1e; border:1px solid #333; border-radius:6px; padding:5px 8px; }
QPushButton { background:#2a2a2a; border:1px solid #3a3a3a; border-radius:6px; padding:7px 14px; }
QPushButton:hover { background:#333; }
QPushButton:disabled, QComboBox:disabled, QSlider:disabled { color:#555; }
QPushButton#shutter { background:#e8e8e8; color:#111; font-weight:600; font-size:15px; padding:12px 28px; }
QPushButton#record { color:#ff5555; font-weight:600; font-size:15px; padding:12px 20px; }
QPushButton#record[on="true"] { background:#c62828; color:white; }
QLabel#preview { background:#000; border-radius:8px; color:#555; }
QLabel#cap { color:#888; font-size:11px; }
QLabel#pill { background:#1e1e1e; border:1px solid #2e2e2e; border-radius:10px; padding:3px 10px; font-size:12px; }
QLabel#pill[alert="true"] { background:#5a1d1d; border-color:#c62828; }
QTabWidget::pane { border:none; }
QTabBar::tab { background:transparent; color:#888; padding:6px 14px; border-bottom:2px solid transparent; }
QTabBar::tab:selected { color:#fff; border-bottom:2px solid #e8e8e8; }
QSlider::groove:horizontal { height:4px; background:#333; border-radius:2px; }
QSlider::handle:horizontal { background:#e8e8e8; width:16px; margin:-6px 0; border-radius:8px; }
QListWidget { background:#1a1a1a; border:1px solid #2a2a2a; border-radius:6px; }
"""


class Preview(QLabel):
    """Live view that reports clicks as normalized image coordinates (ignores letterbox bars)."""
    clicked = Signal(float, float)

    def mousePressEvent(self, e):
        pix = self.pixmap()
        if pix is None or pix.isNull():
            return
        ox = (self.width() - pix.width()) / 2
        oy = (self.height() - pix.height()) / 2
        x = (e.position().x() - ox) / pix.width()
        y = (e.position().y() - oy) / pix.height()
        if 0 <= x <= 1 and 0 <= y <= 1:
            self.clicked.emit(x, y)


def pill(text: str = "") -> QLabel:
    p = QLabel(text)
    p.setObjectName("pill")
    return p


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("camctl")
        self.t = None
        self.caps: set[str] = set()
        self.recording = False
        self.loading = False

        # Connection bar
        self.kind = QComboBox()
        self.kind.addItems(list(transports.REGISTRY))
        self.kind.currentTextChanged.connect(lambda k: self.arg.setText(DEFAULT_ARG.get(k, "")))
        self.arg = QLineEdit()
        self.arg.setPlaceholderText("IP / BLE address / video index")
        self.conn_btn = QPushButton("Connect")
        self.conn_btn.clicked.connect(self.toggle_connect)
        top = QHBoxLayout()
        top.addWidget(self.kind)
        top.addWidget(self.arg, 1)
        top.addWidget(self.conn_btn)
        self.framegen = QCheckBox("2× frames")
        self.framegen.setToolTip("Frame generation: insert an optical-flow frame between each captured pair")
        self.framegen.setVisible(False)
        self.framegen.toggled.connect(self.set_framegen)
        top.addWidget(self.framegen)

        # Monitoring overlays (Phase 1)
        self.ov_hist, self.ov_af = QCheckBox("Histogram"), QCheckBox("AF boxes")
        self.ov_peak, self.ov_zebra = QCheckBox("Peaking"), QCheckBox("Zebras")
        self.ov_hist.setToolTip("Brightness histogram from the camera (click again for RGB)")
        self.ov_peak.setToolTip("Highlight the sharpest edges in red")
        self.ov_zebra.setToolTip("Stripe areas above 95% brightness")
        self.ov_af.setChecked(True)
        self._info, self._info_tick, self._zebra_phase = None, 0, 0

        # Status pills
        self.model = QLabel("Disconnected")
        self.model.setObjectName("cap")
        self.p_bat, self.p_card, self.p_lens, self.p_rec = pill(), pill(), pill(), pill()
        self.p_exp = pill()
        self.p_exp.setToolTip("Exposure the camera actually used for the last shot / metering")
        self.hub = None
        self._rec_t0 = None
        status = QHBoxLayout()
        status.addWidget(self.model)
        status.addStretch()
        for p in (self.p_exp, self.p_lens, self.p_card, self.p_bat, self.p_rec):
            status.addWidget(p)

        # Preview
        self.preview = Preview("No live view")
        self.preview.setObjectName("preview")
        self.preview.setAlignment(Qt.AlignCenter)
        self.preview.setMinimumSize(640, 360)
        self.preview.setToolTip("Click to focus here")
        self.preview.clicked.connect(self.focus_at)

        # Zoom row
        self.zoom = QSlider(Qt.Horizontal)
        self.zoom.setRange(0, 100)
        self.zoom.valueChanged.connect(lambda v: self.t and self.safe(lambda: self.t.zoom(v)))
        self.af_btn = QPushButton("AF")
        self.af_btn.setToolTip("Focus without taking a picture")
        self.af_btn.clicked.connect(lambda: self.safe(self.t.half_press))
        # Continuous power zoom (real R50 V): hold W/T to move, release to stop
        self.wide_btn, self.tele_btn = QPushButton("◀ W"), QPushButton("T ▶")
        for b, d in ((self.wide_btn, "wide"), (self.tele_btn, "tele")):
            b.setToolTip("Hold to zoom")
            b.pressed.connect(lambda d=d: self.t and self.safe(lambda: self.t.zoom_move(d)))
            b.released.connect(lambda: self.t and self.safe(lambda: self.t.zoom_move("stop")))
        self.zoom_w, self.zoom_t = QLabel("W"), QLabel("T")
        zoom_row = QHBoxLayout()
        for w in (self.wide_btn, self.zoom_w, self.zoom, self.zoom_t, self.tele_btn):
            zoom_row.addWidget(w)
        zoom_row.addStretch()
        for cb in (self.ov_hist, self.ov_af, self.ov_peak, self.ov_zebra):
            zoom_row.addWidget(cb)
        zoom_row.addWidget(self.af_btn)

        # Settings tabs
        self.boxes: dict[str, QComboBox] = {}
        self.tabs = QTabWidget()
        self.tabs.addTab(self.settings_grid(MOVIE), "Movie")
        self.tabs.addTab(self.settings_grid(PHOTO), "Photo")
        color = self.settings_grid(COLOR)
        self.lut = None
        self.lut_btn = QPushButton("Load LUT…")
        self.lut_btn.setToolTip("Preview a .cube LUT on live view (e.g. Canon's C-Log3 → Rec.709). Recording is unchanged.")
        self.lut_btn.clicked.connect(self.toggle_lut)
        color.layout().addWidget(self.lut_btn, 1, len(COLOR))
        self.tabs.addTab(color, "Color")
        self.tools = ToolsTab(lambda: {k: b.currentData() for k, b in self.boxes.items()
                                       if b.isEnabled() and b.currentData() is not None})
        self.tools.message.connect(self.flash)
        self.tabs.addTab(self.tools, "Tools")

        # Controls
        self.shutter = QPushButton("Shoot")
        self.shutter.setObjectName("shutter")
        self.shutter.clicked.connect(self.shoot)
        self.rec = QPushButton("● Rec")
        self.rec.setObjectName("record")
        self.rec.clicked.connect(self.toggle_record)
        self.files_btn = QPushButton("Files")
        self.files_btn.setCheckable(True)
        self.files_btn.toggled.connect(self.toggle_files)
        controls = QHBoxLayout()
        controls.addWidget(self.files_btn)
        controls.addStretch()
        controls.addWidget(self.rec)
        controls.addWidget(self.shutter)

        self.files = FilesPanel()
        self.files.setVisible(False)
        self.files.message.connect(self.flash)

        self.msg = QLabel("")
        self.msg.setObjectName("cap")

        root = QVBoxLayout()
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(10)
        root.addLayout(top)
        root.addLayout(status)
        root.addWidget(self.preview, 1)
        root.addLayout(zoom_row)
        root.addWidget(self.tabs)
        root.addLayout(controls)
        root.addWidget(self.files)
        root.addWidget(self.msg)
        w = QWidget()
        w.setLayout(root)
        self.setCentralWidget(w)
        self.setStyleSheet(STYLE)

        self.live = QTimer(self)
        self.live.timeout.connect(self.next_frame)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self.refresh_status)
        self.rec_timer = QTimer(self)
        self.rec_timer.timeout.connect(self.update_rec_pill)
        self.set_connected(False)
        # Remember the last connection and reconnect to it on launch
        self.prefs = QSettings("camctl", "camctl")
        self._want, self._attempts = None, 0
        self.retry = QTimer(self)
        self.retry.timeout.connect(self.try_reconnect)
        last_kind = self.prefs.value("last_kind", "mock")
        self.kind.setCurrentText(last_kind)
        self.arg.setText(self.prefs.value("last_arg", ""))
        if last_kind != "mock" and self.prefs.value("auto_connect", True, type=bool):
            QTimer.singleShot(300, lambda: self.connect_to(last_kind, self.arg.text().strip(), quiet=True)
                              or self.start_waiting(last_kind, self.arg.text().strip()))

    def settings_grid(self, keys: list[str]) -> QWidget:
        grid = QGridLayout()
        grid.setContentsMargins(0, 8, 0, 0)
        for i, key in enumerate(keys):
            cap = QLabel(LABELS[key])
            cap.setObjectName("cap")
            box = QComboBox()
            box.currentIndexChanged.connect(lambda i, k=key, b=box: self.change_setting(k, b.itemData(i)))
            self.boxes[key] = box
            grid.addWidget(cap, 0, i)
            grid.addWidget(box, 1, i)
        w = QWidget()
        w.setLayout(grid)
        return w

    # helpers
    def set_connected(self, on: bool):
        self.kind.setEnabled(not on)
        self.arg.setEnabled(not on)
        self.conn_btn.setText("Disconnect" if on else "Connect")
        self.shutter.setEnabled(on)
        gates = {self.rec: "record", self.files_btn: "list_files", self.zoom: "zoom", self.af_btn: "half_press",
                 self.wide_btn: "zoom_move", self.tele_btn: "zoom_move"}
        for w, cap in gates.items():
            w.setEnabled(on and cap in self.caps)
        # Show the slider for positional zoom, W/T buttons for continuous zoom
        positional = "zoom" in self.caps or not on
        for w in (self.zoom, self.zoom_w, self.zoom_t):
            w.setVisible(positional)
        for w in (self.wide_btn, self.tele_btn):
            w.setVisible(not positional)
        for b in self.boxes.values():
            b.setEnabled(on and "get_setting" in self.caps)
        for p in (self.p_bat, self.p_card, self.p_lens, self.p_rec, self.p_exp):
            p.setVisible(False)

    def flash(self, msg: str):
        self.msg.setText(msg)

    def safe(self, fn):
        try:
            return fn()
        except NotSupported:
            self.flash(f"Not supported over {self.t.name}")
        except Exception as e:
            self.flash(f"Error: {e}")
        return None

    # connection
    def toggle_connect(self):
        if self.t or self._want:
            self._want = None  # user disconnected: stop auto-reconnecting
            self.retry.stop()
            if self.t:
                self.disconnect()
            else:
                self.set_connected(False)
                self.flash("Disconnected")
            return
        self.connect_to(self.kind.currentText(), self.arg.text().strip())

    def connect_to(self, kind: str, arg: str, quiet: bool = False) -> bool:
        """Connect and remember it; while a connection is wanted, drops are retried every 5 s."""
        kwargs = {"wifi": ({"host": arg.rsplit(":", 1)[0], "port": int(arg.rsplit(":", 1)[1])}
                           if arg.count(":") == 1 else {"host": arg}), "ble": {"address": arg or None},
                  "video": {"index": int(arg) if arg.isdigit() else 0}}.get(kind, {})
        try:
            self.t = transports.load(kind, **kwargs)
            self.t.connect()
        except Exception as e:
            self.t = None
            if not quiet:
                self.flash(f"Connect failed: {e}")
            return False
        self._want = (kind, arg)
        self.retry.stop()
        self.prefs.setValue("last_kind", kind)
        self.prefs.setValue("last_arg", arg)
        self.caps = self.t.capabilities()
        self.set_connected(True)
        self.files.set_transport(self.t, self.caps)
        self.tools.set_transport(self.t)
        if "events" in self.caps:
            self.hub = EventHub(self.t)
            self.hub.event.connect(self.on_event)
            self.hub.start()
        self.model.setText(self.safe(lambda: self.t.info().model) or kind)
        self.load_settings()
        self.refresh_status()
        if hasattr(self.t, "start_liveview"):
            self.safe(self.t.start_liveview)
        self.framegen.setVisible(hasattr(self.t, "framegen"))
        if "liveview_frame" in self.caps:
            self.live.start(33 if self.framegen.isChecked() else 66)
        if "status" in self.caps:
            # With live events, a slow safety poll is enough; the recording timer runs locally
            self.poll.start(15000 if self.hub else 1000)
        self.rec_timer.start(1000)
        self.flash("Connected. Click the preview to focus.")
        return True

    def connection_lost(self, why: str):
        """Called when live view or status fails hard: drop the transport and keep retrying."""
        if not self._want:
            return
        self.disconnect(user=False)
        self._attempts = 0
        self.conn_btn.setText("Stop")
        self.preview.setText("Reconnecting…")
        self.flash(f"Connection lost ({why}). Reconnecting…")
        self.retry.start(5000)

    def try_reconnect(self):
        if not self._want or self.t:
            self.retry.stop()
            return
        self._attempts += 1
        if self.connect_to(*self._want, quiet=True):
            self.flash(f"Reconnected after {self._attempts} attempt(s).")
        else:
            self.flash(f"Waiting for camera… (attempt {self._attempts}; turn it on and reconnect Camera Control API)")

    def start_waiting(self, kind: str, arg: str):
        """Launch-time auto-connect failed: keep trying in the background."""
        self._want, self._attempts = (kind, arg), 0
        self.conn_btn.setText("Stop")
        self.flash(f"Waiting for camera at {arg}…")
        self.retry.start(5000)

    def toggle_lut(self):
        if self.lut is not None:
            self.lut = None
            self.lut_btn.setText("Load LUT…")
            return
        path, _ = QFileDialog.getOpenFileName(self, "Load LUT", "", "3D LUT (*.cube)")
        if not path:
            return
        try:
            self.lut = load_cube(path)
        except Exception as e:
            self.flash(f"LUT failed: {e}")
            return
        self.lut_btn.setText(f"LUT: {Path(path).stem} ✕")

    def set_framegen(self, on: bool):
        if self.t and hasattr(self.t, "framegen"):
            self.t.framegen = on
            if self.live.isActive():
                self.live.start(33 if on else 66)

    def disconnect(self, user: bool = True):
        self.live.stop()
        self.poll.stop()
        self.rec_timer.stop()
        if self.hub:
            self.hub.stop = True
            self.hub = None
        self.tools.set_transport(None)
        self.files.set_transport(None, set())
        self.safe(self.t.disconnect)
        self.t = None
        self.caps = set()
        self.recording = False
        self.update_rec()
        self.preview.clear()
        self.preview.setText("No live view")
        self.model.setText("Disconnected")
        self.set_connected(False)
        self.framegen.setVisible(False)
        self.flash("")

    def load_settings(self):
        self.loading = True
        for key, box in self.boxes.items():
            box.clear()
            try:
                data = self.t.get_setting(key)
                values = [str(v) for v in data.get("ability", [])] or [str(data.get("value"))]
                for v in values:
                    box.addItem(label(key, v), v)
                box.setCurrentIndex(max(box.findData(str(data.get("value", ""))), 0))
                box.setToolTip("")
            except Exception as e:
                box.setEnabled(False)
                locked = "Mode not supported" in str(getattr(getattr(e, "response", None), "text", ""))
                box.setToolTip("Locked in the camera's current shooting mode (use P, Tv, Av or M)" if locked
                               else "Not available on this camera or connection")
        self.loading = False

    def refresh_status(self):
        if not self.t or "status" not in self.caps:
            return
        s = self.safe(self.t.status)
        if not s:
            return
        bat = s.get("battery")
        if bat is not None:
            self.p_bat.setText(f"🔋 {bat}%")
            self.p_bat.setProperty("alert", "true" if bat <= 15 else "false")
            self.repolish(self.p_bat)
            self.p_bat.setVisible(True)
        if s.get("card_free_gb") is not None:
            self.p_card.setText(f"SD {s['card_free_gb']} GB")
            self.p_card.setVisible(True)
        if s.get("focal_mm"):
            self.p_lens.setText(f"{s['focal_mm']} mm")
            self.p_lens.setVisible(True)
        self._hot = s.get("temp", "normal") != "normal"
        self.update_rec_pill()

    def update_rec_pill(self):
        rec = int(time.time() - self._rec_t0) if self.recording and self._rec_t0 else None
        hot = getattr(self, "_hot", False)
        self.p_rec.setVisible(rec is not None or hot)
        self.p_rec.setText((f"● {rec // 60:02d}:{rec % 60:02d}" if rec is not None else "") + ("  🌡 hot" if hot else ""))
        self.p_rec.setProperty("alert", "true")
        self.repolish(self.p_rec)

    def on_event(self, ev: dict):
        """Live camera changes from the EventHub: settings, status, exposure, new files."""
        if not self.t:
            return
        for key, data in ev.get("settings", {}).items():
            box = self.boxes.get(key)
            if box is None or not isinstance(data, dict):
                continue
            self.loading = True
            ability = [str(v) for v in data.get("ability") or []]
            if ability and [box.itemData(i) for i in range(box.count())] != ability:
                box.clear()
                for v in ability:
                    box.addItem(label(key, v), v)
            i = box.findData(str(data.get("value")))
            if i < 0:
                box.addItem(label(key, data.get("value")), str(data.get("value")))
                i = box.count() - 1
            box.setCurrentIndex(i)
            box.setEnabled(True)
            self.loading = False
        st = ev.get("status", {})
        if st.get("battery") is not None:
            self.p_bat.setText(f"🔋 {st['battery']}%")
            self.p_bat.setProperty("alert", "true" if st["battery"] <= 15 else "false")
            self.repolish(self.p_bat)
            self.p_bat.setVisible(True)
        if st.get("card_free_gb") is not None:
            left = f" · {st['shots_left']} shots" if st.get("shots_left") else ""
            self.p_card.setText(f"SD {st['card_free_gb']} GB{left}")
            self.p_card.setVisible(True)
        if "temp" in st:
            self._hot = st["temp"] != "normal"
            self.update_rec_pill()
        eff = ev.get("effective")
        if eff:
            self._eff = {**getattr(self, "_eff", {}), **eff}
            e = self._eff
            parts = [label("tv", e["tv"]) if e.get("tv") else None, label("av", e["av"]) if e.get("av") else None,
                     f"ISO {e['iso']}" if e.get("iso") else None]
            self.p_exp.setText("  ".join(p for p in parts if p))
            self.p_exp.setVisible(True)
        if ev.get("added"):
            self.files.on_new_files(ev["added"])

    @staticmethod
    def repolish(w):
        w.style().unpolish(w)
        w.style().polish(w)

    # actions
    def change_setting(self, key: str, value: str):
        if self.t and not self.loading and value:
            if self.safe(lambda: self.t.set_setting(key, value)) is None and self.msg.text().startswith("Error"):
                self.load_settings()

    def focus_at(self, x: float, y: float):
        if self.t and "focus_at" in self.caps:
            self.safe(lambda: self.t.focus_at(x, y))
            self.flash(f"Focus at {x:.2f}, {y:.2f}")

    def shoot(self):
        self.safe(self.t.shoot)
        # With auto-import on, the new-file watcher adds the shot; otherwise reload the grid
        if self.files.isVisible() and not self.files.auto.isChecked():
            self.files.refresh()

    def toggle_record(self):
        self.recording = not self.recording
        self.safe(lambda: self.t.record(self.recording))
        self._rec_t0 = time.time() if self.recording else None
        self.update_rec()
        self.update_rec_pill()

    def update_rec(self):
        self.rec.setText("■ Stop" if self.recording else "● Rec")
        self.rec.setProperty("on", "true" if self.recording else "false")
        self.repolish(self.rec)

    def toggle_files(self, on: bool):
        self.files.setVisible(on)
        if on and self.t and not self.files.items:
            self.files.refresh()

    def next_frame(self):
        try:
            jpg = self.t.liveview_frame()
            want_info = (self.ov_hist.isChecked() or self.ov_af.isChecked()) and "liveview_info" in self.caps
            if self.lut is not None or want_info or self.ov_peak.isChecked() or self.ov_zebra.isChecked():
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if self.lut is not None:
                    frame = apply_lut(frame, self.lut)
                frame = self.apply_overlays(frame, want_info)
                jpg = cv2.imencode(".jpg", frame)[1].tobytes()
        except Exception as e:
            self.live.stop()
            if self._want and not isinstance(e, NotSupported):
                self.connection_lost(str(e)[:60])
            else:
                self.flash(f"Live view stopped: {e}")
            return
        pix = QPixmap.fromImage(QImage.fromData(jpg))
        self.preview.setPixmap(pix.scaled(self.preview.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def apply_overlays(self, frame, want_info: bool):
        # Peaking and zebras are computed locally; histogram and AF boxes come from the camera
        # (fetched every 3rd frame to keep live view fast; ~24 ms per request on the R50 V).
        if self.ov_peak.isChecked():
            frame = overlays.focus_peaking(frame)
        if self.ov_zebra.isChecked():
            self._zebra_phase = (self._zebra_phase + 2) % 12
            frame = overlays.zebras(frame, phase=self._zebra_phase)
        if want_info:
            self._info_tick += 1
            if self._info is None or self._info_tick % 3 == 0:
                try:
                    self._info = self.t.liveview_info()
                except Exception:
                    self._info = self._info or {}
            info = self._info or {}
            if self.ov_af.isChecked():
                frame = overlays.draw_af_frames(frame, info.get("afframe", []), info.get("image", {}))
        if self.ov_hist.isChecked():
            hist = (self._info or {}).get("histogram") if want_info else None
            frame = overlays.draw_histogram(frame, hist or overlays.histogram_from_frame(frame))
        return frame

    def closeEvent(self, e):
        self._want = None
        self.retry.stop()
        if self.t:
            self.disconnect()
        self.files.wait_retired()
        super().closeEvent(e)


def run():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.resize(780, 720)
    win.show()
    sys.exit(app.exec())
