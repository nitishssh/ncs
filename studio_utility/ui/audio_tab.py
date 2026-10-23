"""Audio tab: two mic slots (e.g. DJI Mic receiver + Kreo Sonik) with live meters, noise profiles,
a live voice chain sent to any outputs, and recordings that are cleaned automatically when they stop."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QMenu, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget)

from studio_utility import audio, voice
from studio_utility.paths import PREFS

FLOOR = -60  # meter range: -60 .. 0 dBFS
NOISE_SECONDS = 10

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


class _Bridge(QObject):
    """Background work (noise capture, cleaning) reports back on the Qt thread."""
    done = Signal(str)


class MicSlot(QWidget):
    """Device picker, L/R meters, clip light, noise profile and output routing for one receiver."""
    message = Signal(str)
    changed = Signal()

    def __init__(self, title: str, prefer: str, voice_settings):
        super().__init__()
        self.title, self.prefer, self.mic = title, prefer, None
        self.voice_settings = voice_settings  # callable -> VoiceSettings
        self.prefs = QSettings(*PREFS)
        self.bridge = _Bridge()
        self.bridge.done.connect(self._learned)

        self.device = QComboBox()
        self.device.setMinimumWidth(200)
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

        self.learn = QPushButton(f"Learn noise ({NOISE_SECONDS} s)")
        self.learn.setToolTip("Stay silent: records the room's background noise so it can be removed (like Audacity)")
        self.learn.clicked.connect(self.learn_noise)
        self.noise_state = _cap("")
        self.send = QPushButton("Send to")
        self.send.setToolTip("Play the processed voice on outputs: headphones, a receiver's output into the "
                             "camera's MIC IN, or BlackHole for OBS/Zoom")
        self.send_menu = QMenu(self)
        self.send.setMenu(self.send_menu)

        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.addWidget(_cap(title), 0, 0)
        g.addWidget(self.device, 0, 1, 1, 2)
        g.addWidget(self.clip, 0, 3)
        for i, (m, d) in enumerate(zip(self.meters, self.db)):
            g.addWidget(_cap("L" if i == 0 else "R"), 1 + i, 0, Qt.AlignRight)
            g.addWidget(m, 1 + i, 1, 1, 2)
            g.addWidget(d, 1 + i, 3)
        g.addWidget(self.learn, 3, 1)
        g.addWidget(self.send, 3, 2)
        g.addWidget(self.noise_state, 4, 1, 1, 3)
        g.setColumnStretch(1, 1)

    @property
    def key(self) -> str:
        return self.title.replace(" ", "").lower()

    # devices
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
        self.fill_outputs()
        self.open_device()

    def fill_outputs(self):
        chosen = set(self.prefs.value(f"audio_out_{self.key}", [], type=list) or [])
        self.send_menu.clear()
        for name in audio.output_devices():
            act = self.send_menu.addAction(name)
            act.setCheckable(True)
            act.setChecked(name in chosen)
            act.toggled.connect(self.apply_outputs)

    def chosen_outputs(self) -> list[str]:
        return [a.text() for a in self.send_menu.actions() if a.isChecked()]

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
        self.learn.setEnabled(self.mic is not None)
        self.apply_voice()
        self.apply_outputs()
        self.show_noise_state()
        self.changed.emit()

    # voice chain + outputs
    def apply_voice(self):
        if self.mic:
            self.mic.process = voice.LiveVoice(self.voice_settings(), voice.load_profile(self.mic.device))

    def apply_outputs(self):
        outs = self.chosen_outputs()
        self.prefs.setValue(f"audio_out_{self.key}", outs)
        self.send.setText(f"Send to ({len(outs)})" if outs else "Send to")
        if self.mic:
            self.mic.set_outputs(outs)
            missing = set(outs) - {r.device for r in self.mic.outputs}
            if missing:
                self.message.emit(f"{self.title}: couldn't open {', '.join(missing)}")

    # noise profile
    def show_noise_state(self):
        prof = voice.load_profile(self.mic.device) if self.mic else None
        self.noise_state.setText(f"Noise profile: {voice.noise_floor_db(prof):.0f} dB room tone"
                                 if prof is not None else ("No noise profile yet" if self.mic else ""))

    def learn_noise(self):
        if not self.mic:
            return
        mic = self.mic
        self.learn.setEnabled(False)
        self.learn.setText("Listening… stay silent")

        def work():
            noise = mic.capture(NOISE_SECONDS)
            voice.save_profile(mic.device, noise)
            self.bridge.done.emit(f"{voice.noise_floor_db(noise):.0f}")
        threading.Thread(target=work, daemon=True).start()

    def _learned(self, floor: str):
        self.learn.setEnabled(True)
        self.learn.setText(f"Learn noise ({NOISE_SECONDS} s)")
        self.show_noise_state()
        self.apply_voice()  # the gate threshold follows the new noise floor
        self.message.emit(f"{self.title}: learned room noise at {floor} dB")

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


class VoicePanel(QWidget):
    """Preset + every setting of the voice chain. Changes apply live and are remembered."""
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.settings = voice.load_settings()
        self.preset = QComboBox()
        self.preset.addItems(list(voice.PRESETS) + ["Custom"])
        self.preset.setCurrentText("Custom")
        self.preset.activated.connect(self.pick_preset)
        self.denoise = QSlider(Qt.Horizontal)
        self.denoise.setRange(0, 100)
        self.denoise.setToolTip("How much of the learned noise is removed in the saved _voice.wav")
        self.low_cut = QDoubleSpinBox(suffix=" Hz", decimals=0, minimum=20, maximum=300, singleStep=10)
        self.gate = QCheckBox("Gate")
        self.gate.setToolTip("Silence background between phrases (threshold follows the noise profile)")
        self.presence = QDoubleSpinBox(suffix=" dB", decimals=1, minimum=-6, maximum=8, singleStep=0.5)
        self.air = QDoubleSpinBox(suffix=" dB", decimals=1, minimum=-6, maximum=6, singleStep=0.5)
        self.compress = QComboBox()
        self.compress.addItems(list(voice.COMPRESSION))
        self.gain = QDoubleSpinBox(suffix=" dB", decimals=1, minimum=-12, maximum=18, singleStep=0.5)
        self.enabled = QCheckBox("Voice processing")
        self.vst_btn = QPushButton("+ VST3…")
        self.vst_btn.setToolTip("Add any VST3 effect to the chain (EQ, de-esser, reverb…)")
        self.vst_btn.clicked.connect(self.add_vst)
        self.vst_clear = QPushButton("Clear plugins")
        self.vst_clear.clicked.connect(self.clear_vst)
        self.vst_label = _cap("")
        self.auto_clean = QCheckBox("Auto-clean recordings (_voice.wav)")
        self.auto_clean.setToolTip("When a recording stops, also save a denoised, processed copy - no editing later")
        self.auto_clean.setChecked(QSettings(*PREFS).value("audio_auto_clean", True, type=bool))
        self.auto_clean.toggled.connect(lambda on: QSettings(*PREFS).setValue("audio_auto_clean", on))

        g = QGridLayout(self)
        g.setContentsMargins(0, 6, 0, 0)
        row = [(self.enabled, None), (_cap("Preset"), self.preset), (_cap("Noise removal"), self.denoise),
               (_cap("Low cut"), self.low_cut), (self.gate, None)]
        row2 = [(_cap("Presence"), self.presence), (_cap("Air"), self.air), (_cap("Compression"), self.compress),
                (_cap("Gain"), self.gain), (self.vst_btn, self.vst_clear)]
        for r, items in enumerate((row, row2)):
            c = 0
            for a, b in items:
                g.addWidget(a, r, c)
                if b is not None:
                    g.addWidget(b, r, c + 1)
                c += 2
        g.addWidget(self.vst_label, 2, 0, 1, 6)
        g.addWidget(self.auto_clean, 2, 6, 1, 4)

        self.show_settings(self.settings)
        for w in (self.denoise, self.low_cut, self.presence, self.air, self.gain):
            w.valueChanged.connect(self.edited)
        for w in (self.gate, self.enabled):
            w.toggled.connect(self.edited)
        self.compress.currentIndexChanged.connect(self.edited)

    def show_settings(self, s: voice.VoiceSettings):
        widgets = (self.denoise, self.low_cut, self.presence, self.air, self.gain, self.gate, self.enabled,
                   self.compress)
        for w in widgets:
            w.blockSignals(True)
        self.denoise.setValue(int(s.denoise * 100))
        self.low_cut.setValue(s.low_cut_hz)
        self.presence.setValue(s.presence_db)
        self.air.setValue(s.air_db)
        self.gain.setValue(s.gain_db)
        self.gate.setChecked(s.gate)
        self.enabled.setChecked(s.enabled)
        self.compress.setCurrentText(s.compress)
        for w in widgets:
            w.blockSignals(False)
        self.vst_label.setText("Plugins: " + ", ".join(Path(p).stem for p in s.vst3) if s.vst3 else "")
        for name, p in voice.PRESETS.items():
            if replace(p, vst3=s.vst3) == s:
                self.preset.setCurrentText(name)
                break
        else:
            self.preset.setCurrentText("Custom")

    def edited(self):
        self.settings = voice.VoiceSettings(
            enabled=self.enabled.isChecked(), denoise=self.denoise.value() / 100, low_cut_hz=self.low_cut.value(),
            gate=self.gate.isChecked(), presence_db=self.presence.value(), air_db=self.air.value(),
            compress=self.compress.currentText(), gain_db=self.gain.value(), vst3=self.settings.vst3)
        self.commit()

    def pick_preset(self):
        name = self.preset.currentText()
        if name in voice.PRESETS:
            self.settings = replace(voice.PRESETS[name], vst3=self.settings.vst3)
            self.commit()

    def add_vst(self):
        path, _ = QFileDialog.getOpenFileName(self, "Add VST3 plugin", "/Library/Audio/Plug-Ins/VST3", "VST3 (*.vst3)")
        if path:
            try:
                voice.pb.load_plugin(path)
            except Exception as e:
                self.vst_label.setText(f"Couldn't load {Path(path).name}: {e}")
                return
            self.settings = replace(self.settings, vst3=self.settings.vst3 + [path])
            self.commit()

    def clear_vst(self):
        self.settings = replace(self.settings, vst3=[])
        self.commit()

    def commit(self):
        voice.save_settings(self.settings)
        self.show_settings(self.settings)
        self.changed.emit()


class AudioTab(QWidget):
    message = Signal(str)

    def __init__(self, folder):
        """folder(): directory for the WAV files (the Files panel's dated import folder)."""
        super().__init__()
        self.folder = folder
        self.prefs = QSettings(*PREFS)
        self.available = audio.sd is not None
        self.voice = VoicePanel() if voice.pb is not None else None
        settings = lambda: self.voice.settings if self.voice else voice.VoiceSettings(enabled=False)
        self.slots = [MicSlot("Mic A", "Wireless Mic Rx", settings), MicSlot("Mic B", "Kreo Sonik", settings)]
        self.bridge = _Bridge()
        self.bridge.done.connect(self.message)
        for s in self.slots:
            s.message.connect(self.message)
            s.device.currentIndexChanged.connect(self.save_choice)
        if self.voice:
            self.voice.changed.connect(lambda: [s.apply_voice() for s in self.slots])
        self.with_camera = QCheckBox("Record with camera")
        self.with_camera.setToolTip("Start/stop WAV recording on both mics when the camera's Rec starts/stops")
        self.with_camera.setChecked(self.prefs.value("audio_with_camera", True, type=bool))
        self.with_camera.toggled.connect(lambda on: self.prefs.setValue("audio_with_camera", on))
        self.rec_btn = QPushButton("● Record audio")
        self.rec_btn.clicked.connect(lambda: self.record(not self.recording))
        self.rescan = QPushButton("Rescan")
        self.rescan.setToolTip("Look for newly plugged-in receivers and outputs")
        self.rescan.clicked.connect(self.load_devices)
        self.state = _cap("" if self.available else "Unavailable: install sounddevice + soundfile")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 8, 0, 0)
        row = QHBoxLayout()
        for s in self.slots:
            row.addWidget(s, 1)
        lay.addLayout(row)
        if self.voice:
            lay.addWidget(self.voice)
        row = QHBoxLayout()
        for w in (self.with_camera, self.rec_btn, self.rescan):
            row.addWidget(w)
        row.addStretch()
        row.addWidget(self.state)
        lay.addLayout(row)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        if self.available:
            self.load_devices()
            self.timer.start(50)
        else:
            for w in (self.rec_btn, self.rescan, self.with_camera):
                w.setEnabled(False)

    def load_devices(self):
        for i, s in enumerate(self.slots):
            s.fill(self.prefs.value(f"audio_slot{i}", None))

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
            done = [(s.mic.stop_recording(), s.mic.device) for s in self.slots if s.mic and s.mic.recording]
            if done:
                self.message.emit("Saved audio: " + ", ".join(p.name for p, _ in done))
                if self.voice and self.voice.auto_clean.isChecked() and self.voice.settings.enabled:
                    self.clean_async(done)
        self.rec_btn.setText("■ Stop audio" if self.recording else "● Record audio")

    def clean_async(self, done: list[tuple[Path, str]]):
        """Write <name>_voice.wav next to each raw recording, in the background."""
        settings = self.voice.settings

        def work():
            out = []
            for path, device in done:
                try:
                    out.append(voice.clean_file(path, settings, voice.load_profile(device)).name)
                except Exception as e:
                    out.append(f"{path.name} failed: {e}")
            self.bridge.done.emit("Cleaned voice: " + ", ".join(out))
        threading.Thread(target=work, daemon=True).start()

    def camera_record(self, start: bool):
        """Called by the main window when the camera's Rec starts/stops."""
        if self.with_camera.isChecked() and self.available:
            self.record(start)

    def tick(self):
        for s in self.slots:
            s.tick()
        if self.recording:
            t0 = min(s.mic.started_at for s in self.slots if s.mic and s.mic.recording)
            secs = int(time.time() - t0)
            self.state.setText(f"● REC audio {secs // 60:02d}:{secs % 60:02d}")
        elif self.available:
            self.state.setText("")

    def shutdown(self):
        if self.recording:
            self.record(False)
        self.timer.stop()
        for s in self.slots:
            s.close()
