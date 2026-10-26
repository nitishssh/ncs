"""Creator Panel - minimal PySide6 control UI, same code on macOS and Windows."""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QMenu, QCheckBox, QComboBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QListWidget, QMainWindow, QPushButton, QScrollArea, QSlider, QTabWidget,
                               QVBoxLayout, QWidget)

import cv2
import numpy as np
import shiboken6

from studio_utility import transports
from studio_utility.lut import BUILTIN as BUILTIN_LUTS, apply_lut, clog3_to_rec709, load_cube, write_cube
from studio_utility.core import NotSupported
from studio_utility.paths import PREFS, migrate
from studio_utility import overlays
from studio_utility.labels import label
from studio_utility.ui.files import FilesPanel
from studio_utility.ui.events import EventHub
from studio_utility.ui.tools import ToolsTab
from studio_utility.ui.audio_tab import AudioTab
from studio_utility.ui.workers import CameraExecutor
from studio_utility.ui.connection import ConnectionDialog
from studio_utility.ui import onboarding

log = logging.getLogger(__name__)

PHOTO = ["mode", "tv", "av", "iso", "exposure", "wb", "af_method"]
MOVIE = ["movie_mode", "movie_size", "movie_format", "movie_af"]
COLOR = ["canon_log", "picture_style", "log_assist", "hdr_pq"]
USB = ["zoom_speed", "color_temp", "wb_shift_ab", "wb_shift_gm", "color_filter", "movie_servo_af",
       "clip_prefix", "clip_name"]  # only over USB (Canon EDSDK / gphoto2)
CAMERA_AUDIO = ["sound_recording", "sound_level", "wind_filter", "attenuator"]  # shown in the Audio tab
TEXT = {"clip_prefix", "clip_name"}
LABELS = {"exposure": "Exp. comp", "af_method": "AF method", "mode": "Mode", "tv": "Shutter", "av": "Aperture", "iso": "ISO", "wb": "WB",
          "movie_mode": "Movie mode", "movie_size": "Size / fps", "movie_format": "Format",
          "movie_af": "Movie AF", "canon_log": "Canon Log", "picture_style": "Picture Style",
          "log_assist": "View Assist", "hdr_pq": "HDR PQ",
          "zoom_speed": "Zoom speed", "color_temp": "Color temp", "wb_shift_ab": "WB shift B/A",
          "wb_shift_gm": "WB shift M/G", "color_filter": "Color filter", "movie_servo_af": "Servo AF",
          "clip_prefix": "Clip prefix", "clip_name": "Clip name",
          "sound_recording": "Sound rec.", "sound_level": "Rec. level", "wind_filter": "Wind filter",
          "attenuator": "Attenuator"}
DEFAULT_ARG = {"hub": "", "mock": "", "wifi": "10.11.0.2:8090", "ble": "", "usb-edsdk": "", "usb-gphoto": "", "video": "0"}

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
        self.setWindowTitle("Studio Utility")
        self.t = None
        self.caps: set[str] = set()
        self.recording = False
        self.loading = False
        self._connecting = False
        self.io = CameraExecutor(parent=self)

        # Connection bar
        self.kind = QComboBox()
        self.kind.addItems(list(transports.REGISTRY))
        self.kind.currentTextChanged.connect(lambda k: self.arg.setText(DEFAULT_ARG.get(k, "")))
        self.arg = QLineEdit()
        self.arg.setPlaceholderText("IP / BLE address / video index")
        self.conn_btn = QPushButton("Connect")
        self.conn_btn.clicked.connect(self.toggle_connect)
        self.setup_btn = QPushButton("Setup…")
        self.setup_btn.clicked.connect(self.open_connection_setup)
        top = QHBoxLayout()
        top.addWidget(self.kind)
        top.addWidget(self.arg, 1)
        top.addWidget(self.conn_btn)
        top.addWidget(self.setup_btn)
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
        self.zoom.valueChanged.connect(lambda v: self.run_camera("zoom", lambda: self.t.zoom(v)))
        self.af_btn = QPushButton("AF")
        self.af_btn.setToolTip("Focus without taking a picture")
        self.af_btn.clicked.connect(lambda: self.run_camera("focus", self.t.half_press))
        # Continuous power zoom (real R50 V): hold W/T to move, release to stop
        self.wide_btn, self.tele_btn = QPushButton("◀ W"), QPushButton("T ▶")
        for b, d in ((self.wide_btn, "wide"), (self.tele_btn, "tele")):
            b.setToolTip("Hold to zoom")
            b.pressed.connect(lambda d=d: self.run_camera("zoom-move", lambda: self.t.zoom_move(d)))
            b.released.connect(lambda: self.run_camera("zoom-stop", lambda: self.t.zoom_move("stop")))
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
        self.captions: dict[str, QLabel] = {}
        self.tabs = QTabWidget()
        self.tabs.addTab(self.settings_grid(MOVIE), "Movie")
        self.tabs.addTab(self.settings_grid(PHOTO), "Photo")
        color = self.settings_grid(COLOR)
        self.lut = None
        self.lut_btn = QPushButton("LUT: off")
        self.lut_btn.setToolTip("Preview how Canon Log footage will look after grading. Recording stays log.")
        lut_menu = QMenu(self.lut_btn)
        for name in BUILTIN_LUTS:
            lut_menu.addAction(name, lambda n=name: self.set_lut(BUILTIN_LUTS[n](), n))
        lut_menu.addAction("Load .cube file… (e.g. Canon's official LUT)", self.load_lut_file)
        lut_menu.addSeparator()
        lut_menu.addAction("Export Canon Log 3 → Rec.709 .cube for editing…", self.export_lut)
        lut_menu.addAction("Off", lambda: self.set_lut(None, None))
        self.lut_btn.setMenu(lut_menu)
        color.layout().addWidget(self.lut_btn, 1, len(COLOR))
        self.tabs.addTab(color, "Color")
        usb = self.settings_grid(USB)
        focus_row = QHBoxLayout()
        cap = QLabel("Focus")
        cap.setObjectName("cap")
        focus_row.addWidget(cap)
        self.focus_btns = []
        for step, text in ((-3, "‹‹‹"), (-2, "‹‹"), (-1, "‹"), (1, "›"), (2, "››"), (3, "›››")):
            b = QPushButton(text)
            b.setToolTip(f"{'Nearer' if step < 0 else 'Farther'}, {'fine' if abs(step) == 1 else 'medium' if abs(step) == 2 else 'coarse'} step (live view on)")
            b.clicked.connect(lambda _c=False, s=step: self.focus_drive(s))
            self.focus_btns.append(b)
            focus_row.addWidget(b)
        focus_row.addStretch()
        usb.layout().addLayout(focus_row, 2, 0, 1, len(USB))
        self.usb_tab = self.tabs.addTab(usb, "USB")
        self.tools = ToolsTab(lambda: {k: b.currentData() for k, b in self.boxes.items()
                                       if b.isEnabled() and b.currentData() is not None})
        self.tools.message.connect(self.flash)
        self.tabs.addTab(self.tools, "Tools")
        # External mics (DJI Mic, Kreo Sonik…) record WAVs into the same dated folder as imports
        self.audio = AudioTab(lambda: self.files.dest(), camera_settings=self.settings_grid(CAMERA_AUDIO))
        self.audio.message.connect(self.flash)
        audio_scroll = QScrollArea()  # mics + voice chain + EQ are taller than a laptop screen leaves for a tab
        audio_scroll.setWidget(self.audio)
        audio_scroll.setWidgetResizable(True)
        audio_scroll.setFrameShape(QScrollArea.NoFrame)
        self.tabs.addTab(audio_scroll, "Audio")

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
        self.prefs = QSettings(*PREFS)
        self._want, self._attempts = None, 0
        self.retry = QTimer(self)
        self.retry.timeout.connect(self.try_reconnect)
        last_kind = self.prefs.value("last_kind", "mock")
        self.kind.setCurrentText(last_kind)
        self.arg.setText(self.prefs.value("last_arg", ""))
        if not self.prefs.value("onboarded", False, type=bool):
            pass  # run() shows onboarding first and connects to what it picks
        elif last_kind != "mock" and self.prefs.value("auto_connect", True, type=bool):
            QTimer.singleShot(300, lambda: self.connect_to(last_kind, self.arg.text().strip(), quiet=True))

    def settings_grid(self, keys: list[str]) -> QWidget:
        grid = QGridLayout()
        grid.setContentsMargins(0, 8, 0, 0)
        for i, key in enumerate(keys):
            cap = QLabel(LABELS[key])
            cap.setObjectName("cap")
            box = QComboBox()
            if key in TEXT:  # free text, written when editing finishes
                box.setEditable(True)
                box.lineEdit().editingFinished.connect(lambda k=key, b=box: self.change_setting(k, b.currentText()))
            else:
                box.currentIndexChanged.connect(lambda i, k=key, b=box: self.change_setting(k, b.itemData(i)))
            self.boxes[key] = box
            self.captions[key] = cap
            grid.addWidget(cap, 0, i)
            grid.addWidget(box, 1, i)
        grid.setRowStretch(9, 1)  # keep labels on top of their boxes when the tab is taller
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
        for b in self.focus_btns:
            b.setEnabled(on and "focus_drive" in self.caps)
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

    def run_camera(self, key, fn, done=lambda _value: None, failed=None) -> bool:
        """Run camera I/O without blocking Qt; repeated polling calls are coalesced by key."""
        if not self.t:
            return False

        def report(error):
            log.warning("Camera operation %s failed: %s", key, error, exc_info=(type(error), error, error.__traceback__))
            if failed:
                failed(error)
            elif isinstance(error, NotSupported):
                self.flash(f"Not supported over {self.t.name if self.t else 'this connection'}")
            else:
                self.flash(f"Error: {error}")

        return self.io.submit(key, fn, done, report)

    def run_onboarding(self, force: bool = True):
        choice = onboarding.maybe_show(self, self.prefs, force=force)
        if choice:
            kind, value = choice
            self.kind.setCurrentText(kind)
            self.arg.setText(value)
            if not self.t:
                self.connect_to(kind, value)

    def open_connection_setup(self):
        menu = QMenu(self)
        menu.addAction("Guided setup…", self.run_onboarding)
        menu.addAction("Advanced connection settings…", self.open_advanced_setup)
        menu.exec(self.setup_btn.mapToGlobal(self.setup_btn.rect().bottomLeft()))

    def open_advanced_setup(self):
        dialog = ConnectionDialog(self.kind.currentText(), self.arg.text().strip(), self)
        if dialog.exec():
            kind, value = dialog.selected()
            self.kind.setCurrentText(kind)
            self.arg.setText(value)

    # connection
    def toggle_connect(self):
        if self.t or self._want or self._connecting:
            self._want = None  # user disconnected: stop auto-reconnecting
            self.retry.stop()
            if self.t:
                self.disconnect()
            else:
                self._connecting = False
                self.set_connected(False)
                self.flash("Disconnected")
            return
        self.connect_to(self.kind.currentText(), self.arg.text().strip())

    def connect_to(self, kind: str, arg: str, quiet: bool = False) -> bool:
        """Connect and inspect the camera in a worker, then install it on the Qt thread."""
        try:
            kwargs = {"hub": {"link": arg}, "wifi": ({"host": arg.rsplit(":", 1)[0], "port": int(arg.rsplit(":", 1)[1])}
                               if arg.count(":") == 1 else {"host": arg}), "ble": {"address": arg or None},
                      "video": {"index": int(arg) if arg.isdigit() else 0}}.get(kind, {})
        except (TypeError, ValueError) as error:
            self.flash(f"Connect failed: {error}")
            return False

        self._want = (kind, arg)
        self._connecting = True
        self.retry.stop()
        self.kind.setEnabled(False)
        self.arg.setEnabled(False)
        self.conn_btn.setText("Stop")
        self.flash(f"Connecting to {arg or kind}…")

        def bootstrap():
            transport = transports.load(kind, **kwargs)
            transport.connect()
            caps = transport.capabilities()
            try:
                model = transport.info().model
            except Exception:
                model = kind
            settings = {}
            if "get_setting" in caps:
                for key in self.boxes:
                    try:
                        settings[key] = (transport.get_setting(key), None)
                    except Exception as error:
                        response = getattr(getattr(error, "response", None), "text", "")
                        settings[key] = (None, "locked" if "Mode not supported" in str(response) else "unavailable")
            status = transport.status() if "status" in caps else {}
            if hasattr(transport, "start_liveview"):
                transport.start_liveview()
            return transport, caps, model, settings, status

        started = self.io.submit(
            "connect", bootstrap,
            lambda result: self.connection_ready(kind, arg, result),
            lambda error: self.connection_failed(kind, arg, error, quiet),
        )
        if not started:
            self._connecting = False
            return False
        return True

    def connection_ready(self, kind, arg, result):
        transport, caps, model, settings, status = result
        if self._want != (kind, arg):
            self.io.submit(f"disconnect:{id(transport)}", transport.disconnect, lambda _v: None, lambda _e: None)
            return
        self._connecting = False
        self.t = transport
        self.prefs.setValue("last_kind", kind)
        self.prefs.setValue("last_arg", arg)
        self.caps = caps
        self.set_connected(True)
        self.files.set_transport(self.t, self.caps)
        self.tools.set_transport(self.t)
        if "events" in self.caps:
            self.hub = EventHub(self.t)
            self.hub.event.connect(self.on_event)
            self.hub.start()
        self.model.setText(model)
        self.apply_settings(settings)
        self.show_status(status)
        self.framegen.setVisible(hasattr(self.t, "framegen"))
        if "liveview_frame" in self.caps:
            self.live.start(33 if self.framegen.isChecked() else 66)
        if "status" in self.caps:
            # With live events, a slow safety poll is enough; the recording timer runs locally
            self.poll.start(15000 if self.hub else 1000)
        self.rec_timer.start(1000)
        message = f"Reconnected after {self._attempts} attempt(s)." if self._attempts else "Connected. Click the preview to focus."
        self.flash(message)
        self._attempts = 0

    def connection_failed(self, kind, arg, error, quiet):
        log.warning("Connection via %s failed: %s", kind, error,
                    exc_info=(type(error), error, error.__traceback__))
        if self._want != (kind, arg):
            return
        self._connecting = False
        self.t = None
        self.set_connected(False)
        self.conn_btn.setText("Stop")
        if quiet or self._attempts:
            delay = retry_delay(self._attempts)
            self.flash(f"Waiting for camera… (attempt {self._attempts}, next in {delay // 1000} s; {error})")
            self.retry.start(delay)
        else:
            self._want = None
            self.conn_btn.setText("Connect")
            self.flash(f"Connect failed: {error}")

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
        if self._connecting:
            return
        self._attempts += 1
        self.connect_to(*self._want, quiet=True)

    def set_lut(self, table, name):
        self.lut = table
        self.lut_btn.setText(f"LUT: {name}" if name else "LUT: off")

    def load_lut_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load LUT", "", "3D LUT (*.cube)")
        if not path:
            return
        try:
            self.set_lut(load_cube(path), Path(path).stem)
        except Exception as e:
            self.flash(f"LUT failed: {e}")

    def export_lut(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export LUT", "Canon Log 3 to Rec709 (Studio Utility).cube",
                                              "3D LUT (*.cube)")
        if path:
            write_cube(clog3_to_rec709(), path, "Canon Log 3 to Rec709 (Studio Utility preview)")
            self.flash(f"Saved {path} - load it in DaVinci Resolve, Premiere or Final Cut")

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
        if not self.t or "get_setting" not in self.caps:
            return
        transport = self.t

        def fetch():
            settings = {}
            for key in self.boxes:
                try:
                    settings[key] = (transport.get_setting(key), None)
                except Exception as error:
                    response = getattr(getattr(error, "response", None), "text", "")
                    settings[key] = (None, "locked" if "Mode not supported" in str(response) else "unavailable")
            return settings

        self.run_camera("settings", fetch, self.apply_settings)

    def apply_settings(self, settings):
        self.loading = True
        for key, box in self.boxes.items():
            box.clear()
            data, error = settings.get(key, (None, "unavailable"))
            if data is not None:
                values = [str(v) for v in data.get("ability", [])] or [str(data.get("value"))]
                for v in values:
                    box.addItem(label(key, v), v)
                box.setCurrentIndex(max(box.findData(str(data.get("value", ""))), 0))
                box.setToolTip("")
                box.setEnabled(True)
            else:
                box.setEnabled(False)
                box.setToolTip("Locked in the camera's current shooting mode (use P, Tv, Av or M)" if error == "locked"
                               else "Not available on this camera or connection")
        for k in USB + CAMERA_AUDIO:  # each link offers a different subset: hide what this one lacks
            there = settings.get(k, (None,))[0] is not None
            self.boxes[k].setVisible(there)
            self.captions[k].setVisible(there)
        usb = any(settings.get(k, (None,))[0] is not None for k in USB) or "focus_drive" in self.caps
        self.tabs.setTabVisible(self.usb_tab, usb)  # Wi-Fi has none of these
        self.audio.show_camera_settings(any(settings.get(k, (None,))[0] is not None for k in CAMERA_AUDIO))
        self.loading = False

    def refresh_status(self):
        if not self.t or "status" not in self.caps:
            return
        transport = self.t
        self.run_camera("status", transport.status, self.show_status)

    def show_battery(self, bat):
        """Percent, or unknown (the R50 V reports no level on USB power / while charging)."""
        if isinstance(bat, (int, float)):
            self.p_bat.setText(f"🔋 {bat:.0f}%")
            self.p_bat.setProperty("alert", "true" if bat <= 15 else "false")
        else:
            self.p_bat.setText("🔌 battery –")
            self.p_bat.setToolTip("Camera reports no battery level (USB power or charging)")
            self.p_bat.setProperty("alert", "false")
        self.repolish(self.p_bat)
        self.p_bat.setVisible(True)

    def show_status(self, s):
        if not self.t or not s:
            return
        self.show_battery(s.get("battery"))
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
        if "battery" in st:
            self.show_battery(st["battery"])
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
        if "recording" in ev:  # started/stopped from another device through the hub
            rec = ev["recording"]
            if bool(rec) != self.recording:
                self.recording = bool(rec)
                self._rec_t0 = rec.get("since", time.time()) if rec else None
                self.update_rec()
                self.update_rec_pill()
                self.audio.camera_record(self.recording)  # mics follow the camera wherever Rec was pressed
                if ev.get("by"):
                    self.flash(f"Recording {'started' if rec else 'stopped'} on {ev['by']}")
        if ev.get("camera_lost"):
            self.flash(f"Hub: camera {ev['camera_lost']}")

    @staticmethod
    def repolish(w):
        w.style().unpolish(w)
        w.style().polish(w)

    # actions
    def change_setting(self, key: str, value: str):
        if self.t and not self.loading and value:
            transport = self.t
            self.run_camera(f"setting:{key}", lambda: transport.set_setting(key, value),
                            failed=lambda error: (self.flash(f"Error: {error}"), self.load_settings()))

    def focus_drive(self, step: int):
        if self.t and "focus_drive" in self.caps:
            transport = self.t
            self.run_camera("focus-drive", lambda: transport.focus_drive(step),
                            failed=lambda error: self.flash(f"Focus: {error}"))

    def focus_at(self, x: float, y: float):
        if self.t and "focus_at" in self.caps:
            transport = self.t
            self.run_camera("focus", lambda: transport.focus_at(x, y),
                            done=lambda _value: self.flash(f"Focus at {x:.2f}, {y:.2f}"))

    def shoot(self):
        transport = self.t
        self.shutter.setEnabled(False)

        def shot(_value):
            self.shutter.setEnabled(True)
            if self.files.isVisible() and not self.files.auto.isChecked():
                self.files.refresh()

        self.run_camera("shoot", transport.shoot, shot,
                        lambda error: (self.shutter.setEnabled(True), self.flash(f"Shoot failed: {error}")))

    def toggle_record(self):
        target = not self.recording
        transport = self.t
        self.rec.setEnabled(False)

        def confirmed(_value):
            self.rec.setEnabled(True)
            self.recording = target
            self._rec_t0 = time.time() if target else None
            self.audio.camera_record(target)
            self.update_rec()
            self.update_rec_pill()

        def rejected(error):
            self.rec.setEnabled(True)
            if isinstance(error, NotSupported):
                self.flash(f"Not supported over {transport.name}")
            else:
                self.flash(f"Record command failed: {error}")

        if not self.run_camera("record", lambda: transport.record(target), confirmed, rejected):
            # the previous Rec command hasn't come back from the camera yet: never leave the button dead
            self.rec.setEnabled(True)
            self.flash("The camera is still answering the last Rec command - try again in a moment")

    def update_rec(self):
        self.rec.setText("■ Stop" if self.recording else "● Rec")
        self.rec.setProperty("on", "true" if self.recording else "false")
        self.repolish(self.rec)

    def toggle_files(self, on: bool):
        self.files.setVisible(on)
        if on and self.t and not self.files.items:
            self.files.refresh()

    def next_frame(self):
        if not self.t:
            return
        transport = self.t
        want_info = (self.ov_hist.isChecked() or self.ov_af.isChecked()) and "liveview_info" in self.caps
        self._info_tick += 1
        fetch_info = want_info and (self._info is None or self._info_tick % 3 == 0)

        def fetch():
            return transport.liveview_frame(), transport.liveview_info() if fetch_info else None

        self.run_camera("liveview", fetch, lambda result: self.show_frame(result, want_info), self.liveview_failed)

    def liveview_failed(self, error):
        self.live.stop()
        if self._want and not isinstance(error, NotSupported):
            self.connection_lost(str(error)[:60])
        else:
            self.flash(f"Live view stopped: {error}")

    def show_frame(self, result, want_info):
        if not self.t:
            return
        jpg, info = result
        if info is not None:
            self._info = info
        try:
            if self.lut is not None or want_info or self.ov_peak.isChecked() or self.ov_zebra.isChecked():
                frame = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
                if self.lut is not None:
                    frame = apply_lut(frame, self.lut)
                frame = self.apply_overlays(frame, want_info)
                jpg = cv2.imencode(".jpg", frame)[1].tobytes()
        except Exception as error:
            self.liveview_failed(error)
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
            info = self._info or {}
            if self.ov_af.isChecked():
                frame = overlays.draw_af_frames(frame, info.get("afframe", []), info.get("image", {}))
        if self.ov_hist.isChecked():
            hist = (self._info or {}).get("histogram") if want_info else None
            frame = overlays.draw_histogram(frame, hist or overlays.histogram_from_frame(frame))
        return frame

    def closeEvent(self, e):
        self.io.shutdown()
        self.audio.shutdown()
        self._want = None
        self.retry.stop()
        if self.t:
            self.disconnect()
        self.files.wait_retired()
        super().closeEvent(e)


def retry_delay(attempts: int) -> int:
    """ms until the next reconnect: 5, 10, 20, 40, then every 60 s. Each USB attempt goes through macOS Image
    Capture; retrying a stuck one every 5 s ended in a crash inside ImageCaptureCore (2026-10-27)."""
    return min(5000 * 2 ** max(attempts - 1, 0), 60000)


class _MainCalls(QObject):
    """Runs callables on the GUI thread (queued signal). Canon's SDK on macOS must be driven from there."""
    call = Signal(object)

    @Slot(object)
    def run(self, fn):
        fn()


def _main_thread_calls():
    if sys.platform != "darwin":
        return
    from studio_utility.transports.edsdk import set_main_dispatcher
    relay = _MainCalls(QApplication.instance())
    relay.call.connect(relay.run, Qt.QueuedConnection)  # slot owned by the GUI thread
    set_main_dispatcher(relay.call.emit)


def run():
    from studio_utility.diagnostics import configure_logging
    configure_logging()
    log.info("Studio Utility starting")
    migrate()  # one-time carry-over of settings from the camctl name
    app = QApplication(sys.argv)
    app.setApplicationName("Studio Utility")
    _main_thread_calls()
    win = MainWindow()
    win.resize(780, 720)
    win.show()
    QTimer.singleShot(300, lambda: win.run_onboarding(force=False))  # first launch only
    sys.exit(finish(app, win, app.exec()))


def finish(app, win, code: int) -> int:
    """Destroy the window (and every child) while the QApplication still exists. Left to PySide's exit cleanup it
    was torn down after the app and crashed the process on quit (SIGSEGV in ~QPushButton, 2026-10-27)."""
    shiboken6.delete(win)
    app.processEvents()
    return code
