"""Audio tab: two source slots with live meters, noise profiles, a live voice chain sent to any outputs, and
recordings that are cleaned automatically when they stop. A slot can take the camera's own audio (USB/HDMI),
this computer's mic or an external receiver (DJI Mic, Kreo Sonik); the camera's in-body sound settings and
phone mics recording through the hub are shown alongside."""
from __future__ import annotations

import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QGridLayout, QHBoxLayout, QLabel,
                               QMenu, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget)

from studio_utility import advice, audio, voice
from studio_utility.paths import PREFS

FLOOR = -60  # meter range: -60 .. 0 dBFS
NOISE_SECONDS = 10
PAIRS_PER_ROW = 2  # voice settings grid
ADVICE_EVERY = 3.0  # seconds between live suggestion updates
TIP_COLOR = {"problem": "#ef5350", "warn": "#f9a825", "ok": "#9e9e9e"}

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
    """Background work (noise capture, cleaning, analysis) reports back on the Qt thread."""
    done = Signal(str)
    tips = Signal(object)  # advice.Report
    review = Signal(str)   # after-take report (rich text)


def tips_html(tips) -> str:
    return "<br>".join(f'<span style="color:{TIP_COLOR[t.level]}">●</span> <b>{t.title}</b>'
                       + (f" - {t.fix}" if t.fix else "") for t in tips)


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
        self.bridge.tips.connect(self.show_tips)
        self._analysing, self._last_advice = False, 0.0

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
        self.tips = QLabel("")
        self.tips.setWordWrap(True)
        self.tips.setTextFormat(Qt.RichText)
        self.tips.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        # room for 3 tips (wrapped) up front: tips come and go every few seconds and must not shift the controls below
        self.tips.setFixedHeight(self.tips.fontMetrics().lineSpacing() * 4 + 4)
        self.tips.setToolTip("Suggestions from the last few seconds of this mic: level, room noise, hum, wind, "
                             "harsh 's' sounds. Updated every few seconds.")
        g.addWidget(self.tips, 5, 0, 1, 4)
        g.setColumnStretch(1, 1)

    @property
    def key(self) -> str:
        return self.title.replace(" ", "").lower()

    # devices
    def fill(self, saved: str | None):
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("Off", None)
        kind = None
        for d in audio.input_devices():  # grouped: camera, this computer, external mics, virtual
            if d.kind != kind:
                kind = d.kind
                self.device.insertSeparator(self.device.count())
            self.device.addItem(f"{audio.SOURCE_LABELS[d.kind]}: {d.label}", d.name)
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
        self.tips.setText("")
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
        if len(levels) == 1:  # mono input (laptop mic): same level on both meters
            levels = levels * 2
        silent = all(p <= -119 for _, p in levels)
        for (rms, peak), m, d in zip(levels, self.meters, self.db):
            m.setValue(int(max(FLOOR, min(0, peak))))
            style = meter_style(peak)
            if m.styleSheet() != style:
                m.setStyleSheet(style)
            d.setText("no signal" if silent else f"{peak:5.0f} dB")
        self.clip.setStyleSheet("background:#c62828;color:white;" if self.mic.clipped else "")
        if not self._analysing and time.time() - self._last_advice >= ADVICE_EVERY:
            self.analyse()

    # suggestions
    def analyse(self):
        mic = self.mic
        self._analysing, self._last_advice = True, time.time()

        def work():
            try:
                self.bridge.tips.emit(advice.analyze(mic.recent(), mic.rate, live=True))
            except Exception:
                self.bridge.tips.emit(None)
        threading.Thread(target=work, daemon=True).start()

    def show_tips(self, report):
        self._analysing = False
        if report is None or not self.mic:
            self.tips.setText("")
            return
        self.tips.setText(tips_html(report.tips[:3]))
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
        self.auto_clean = QCheckBox("Auto-clean takes")
        self.auto_clean.setToolTip("When a recording stops, also save a denoised, processed copy - no editing later")
        self.auto_clean.setChecked(QSettings(*PREFS).value("audio_auto_clean", True, type=bool))
        self.auto_clean.toggled.connect(lambda on: QSettings(*PREFS).setValue("audio_auto_clean", on))

        g = QGridLayout(self)
        g.setContentsMargins(0, 6, 0, 0)
        row = [(self.enabled, None), (_cap("Preset"), self.preset), (_cap("Noise removal"), self.denoise),
               (_cap("Low cut"), self.low_cut), (self.gate, None)]
        row2 = [(_cap("Presence"), self.presence), (_cap("Air"), self.air), (_cap("Compression"), self.compress),
                (_cap("Gain"), self.gain), (self.vst_btn, self.vst_clear)]
        # cleanup engine, hum, loudness
        self.engine = QComboBox()
        # sized for the short names, not "... - not installed": the long entry made this tab scroll sideways
        self.engine.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.engine.setMinimumContentsLength(16)
        for key, name in voice.ENGINES.items():
            self.engine.addItem(name, key)
        self.engine.setToolTip("How the saved _voice.wav is denoised. DeepFilterNet removes changing noise (traffic, "
                               "keyboard); RNNoise is light; Noise profile suits steady hiss.")
        self.get_df = QPushButton("Get DeepFilterNet (28 MB)")
        self.get_df.setToolTip("Downloads the official DeepFilterNet build from its GitHub release, checks it and "
                               "installs it for this app only")
        self.get_df.clicked.connect(self.install_df)
        self.bridge = _Bridge()
        self.bridge.done.connect(self._df_installed)
        self.mark_engines()
        self.live_denoise = QCheckBox("AI denoise live")
        self.live_denoise.setToolTip("RNNoise on the live outputs (headphones, OBS…); adds 30 ms")
        self.live_denoise.setEnabled(voice.rnnoise is not None)
        self.hum = QComboBox()
        self.hum.addItems(["off", "50", "60"])
        self.hum.setToolTip("Notch out mains hum: 50 Hz (India, Europe) or 60 Hz (Americas)")
        self.normalize = QCheckBox("Loudness")
        self.normalize.setToolTip("Bring every saved _voice.wav to the same loudness")
        self.target = QDoubleSpinBox(suffix=" LUFS", decimals=0, minimum=-30, maximum=-9, singleStep=1)
        self.target.setToolTip("-16 YouTube/podcasts, -14 Spotify/Instagram, -23 broadcast")
        row3 = [(_cap("Cleanup"), self.engine), (self.get_df, None), (self.live_denoise, None),
                (_cap("Hum"), self.hum), (self.normalize, self.target)]
        # three label/control pairs per row: five per row needed 1314 px and forced a sideways scrollbar
        for i, (a, b) in enumerate(row + row2 + row3):
            r, c = divmod(i, PAIRS_PER_ROW)
            g.addWidget(a, r, c * 2)
            if b is not None:
                g.addWidget(b, r, c * 2 + 1)
        last = (len(row + row2 + row3) - 1) // PAIRS_PER_ROW + 1

        # 10-band EQ
        self.eq_preset = QComboBox()
        self.eq_preset.addItems(list(voice.EQ_PRESETS) + ["Custom"])
        self.eq_preset.activated.connect(self.pick_eq)
        self.eq = []
        eq_row = QHBoxLayout()
        eq_row.addWidget(_cap("EQ"))
        eq_row.addWidget(self.eq_preset)
        for hz in voice.EQ_BANDS:
            col = QVBoxLayout()
            s = QSlider(Qt.Vertical)
            s.setRange(-12, 12)
            s.setFixedHeight(70)
            s.setToolTip(f"{hz} Hz")
            lb = _cap(f"{hz // 1000}k" if hz >= 1000 else str(hz))
            col.addWidget(s, 0, Qt.AlignHCenter)
            col.addWidget(lb, 0, Qt.AlignHCenter)
            eq_row.addLayout(col)
            self.eq.append(s)
        eq_row.addStretch()
        g.addLayout(eq_row, last, 0, 1, PAIRS_PER_ROW * 2)
        g.addWidget(self.vst_label, last + 1, 0, 1, 3)
        g.addWidget(self.auto_clean, last + 1, 3, 1, 3)

        self.show_settings(self.settings)
        for w in (self.denoise, self.low_cut, self.presence, self.air, self.gain, self.target, *self.eq):
            w.valueChanged.connect(self.edited)
        for w in (self.gate, self.enabled, self.live_denoise, self.normalize):
            w.toggled.connect(self.edited)
        for w in (self.compress, self.engine, self.hum):
            w.currentIndexChanged.connect(self.edited)

    def show_settings(self, s: voice.VoiceSettings):
        widgets = (self.denoise, self.low_cut, self.presence, self.air, self.gain, self.gate, self.enabled,
                   self.compress, self.engine, self.hum, self.live_denoise, self.normalize, self.target, *self.eq)
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
        self.engine.setCurrentIndex(max(self.engine.findData(s.engine), 0))
        self.hum.setCurrentText(s.hum)
        self.live_denoise.setChecked(s.live_denoise)
        self.normalize.setChecked(s.normalize)
        self.target.setValue(s.target_lufs)
        self.target.setEnabled(s.normalize)
        for sl, db in zip(self.eq, s.eq):
            sl.setValue(int(round(db)))
        self.eq_preset.setCurrentText(next((n for n, v in voice.EQ_PRESETS.items() if list(v) == list(s.eq)), "Custom"))
        for w in widgets:
            w.blockSignals(False)
        self.vst_label.setText("Plugins: " + ", ".join(Path(p).stem for p in s.vst3) if s.vst3 else "")
        for name, p in voice.PRESETS.items():
            if replace(p, vst3=s.vst3, eq=s.eq, engine=s.engine, live_denoise=s.live_denoise, normalize=s.normalize,
                       target_lufs=s.target_lufs, hum=s.hum) == s:
                self.preset.setCurrentText(name)
                break
        else:
            self.preset.setCurrentText("Custom")

    def edited(self):
        self.settings = voice.VoiceSettings(
            enabled=self.enabled.isChecked(), denoise=self.denoise.value() / 100, low_cut_hz=self.low_cut.value(),
            gate=self.gate.isChecked(), presence_db=self.presence.value(), air_db=self.air.value(),
            compress=self.compress.currentText(), gain_db=self.gain.value(), vst3=self.settings.vst3,
            eq=[float(s.value()) for s in self.eq], hum=self.hum.currentText(), engine=self.engine.currentData(),
            live_denoise=self.live_denoise.isChecked(), normalize=self.normalize.isChecked(),
            target_lufs=self.target.value())
        self.commit()

    def mark_engines(self):
        have = voice.engines()
        for i, key in enumerate(voice.ENGINES):
            name = voice.ENGINES[key]
            self.engine.setItemText(i, name if key in have else f"{name} - not installed")
            self.engine.model().item(i).setEnabled(key in have)
        self.get_df.setVisible("deepfilternet" not in have and voice.can_install_deepfilter())

    def install_df(self):
        self.get_df.setEnabled(False)

        def progress(done, total):
            self.bridge.done.emit(f"progress:{done * 100 // total if total else 0}")

        def work():
            try:
                voice.install_deepfilter(progress)
                self.bridge.done.emit("ok")
            except Exception as e:
                self.bridge.done.emit(f"error:{e}")
        threading.Thread(target=work, daemon=True).start()

    def _df_installed(self, state: str):
        if state.startswith("progress:"):
            self.get_df.setText(f"Downloading… {state.split(':', 1)[1]}%")
            return
        self.get_df.setEnabled(True)
        self.get_df.setText("Get DeepFilterNet (28 MB)")
        if state.startswith("error:"):
            self.vst_label.setText(f"DeepFilterNet: {state.split(':', 1)[1]}")
            return
        self.mark_engines()
        self.settings = replace(self.settings, engine="deepfilternet")
        self.commit()

    def pick_eq(self):
        name = self.eq_preset.currentText()
        if name in voice.EQ_PRESETS:
            self.settings = replace(self.settings, eq=[float(v) for v in voice.EQ_PRESETS[name]])
            self.commit()

    def pick_preset(self):
        name = self.preset.currentText()
        if name in voice.PRESETS:
            keep = {k: getattr(self.settings, k) for k in ("vst3", "eq", "engine", "live_denoise", "normalize",
                                                            "target_lufs", "hum")}
            self.settings = replace(voice.PRESETS[name], **keep)
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

    def __init__(self, folder, camera_settings: QWidget | None = None):
        """folder(): directory for the WAV files (the Files panel's dated import folder).
        camera_settings: the camera's own sound settings (rec. level, wind filter…), driven by the main window."""
        super().__init__()
        self.folder = folder
        self.prefs = QSettings(*PREFS)
        self.available = audio.sd is not None
        self.voice = VoicePanel() if voice.pb is not None else None
        settings = lambda: self.voice.settings if self.voice else voice.VoiceSettings(enabled=False)
        self.slots = [MicSlot("Mic A", "Wireless Mic Rx", settings), MicSlot("Mic B", "Kreo Sonik", settings)]
        self.bridge = _Bridge()
        self.bridge.done.connect(self.message)
        self.bridge.review.connect(lambda html: (self.review.setText("Last take: " + html), self.review.show()))
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
        self.last_takes: list[Path] = []
        self.editor = None
        self.edit_btn = QPushButton("Edit…")
        self.edit_btn.setToolTip("Waveform editor: trim, compare raw and cleaned, re-clean, export "
                                 "(opens the last take, or pick any WAV)")
        self.edit_btn.clicked.connect(lambda: self.open_editor(self.last_takes[0] if self.last_takes else None))
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
        self.camera_box = QWidget()
        cam = QHBoxLayout(self.camera_box)
        cam.setContentsMargins(0, 6, 0, 0)
        cam.addWidget(_cap("Camera audio"))
        if camera_settings is not None:
            cam.addWidget(camera_settings, 1)
        self.camera_box.setToolTip("The R50 V's own sound settings, used for the audio it records into the clip")
        self.camera_box.hide()  # shown once a connected camera answers at least one of them
        lay.addWidget(self.camera_box)
        self.phone = _cap("Phone mic: open the Studio Utility app on the phone and turn on Mic - its recordings "
                          "upload to the hub and line up with clips by time.")
        self.phone.setWordWrap(True)
        lay.addWidget(self.phone)
        self.review = QLabel("")
        self.review.setWordWrap(True)
        self.review.setTextFormat(Qt.RichText)
        self.review.hide()
        lay.addWidget(self.review)
        if self.voice:
            lay.addWidget(self.voice)
        row = QHBoxLayout()
        for w in (self.with_camera, self.rec_btn, self.edit_btn, self.rescan):
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

    def show_camera_settings(self, on: bool):
        self.camera_box.setVisible(on)

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
                self.last_takes = [p for p, _ in done]
                self.message.emit("Saved audio: " + ", ".join(p.name for p, _ in done))
                self.review_async([p for p, _ in done])
                if self.voice and self.voice.auto_clean.isChecked() and self.voice.settings.enabled:
                    self.clean_async(done)
        self.rec_btn.setText("■ Stop audio" if self.recording else "● Record audio")

    def review_async(self, paths: list[Path]):
        """Check each finished take (whole file) and show what to improve next time."""
        def work():
            lines = []
            for p in paths:
                try:
                    rep = advice.analyze_file(p)
                except Exception as e:
                    lines.append(f"<b>{p.name}</b>: couldn't check ({e})")
                    continue
                loud = f", {rep.lufs:.0f} LUFS" if rep.lufs is not None else ""
                lines.append(f"<b>{p.name}</b> (peak {rep.peak_db:.0f} dBFS{loud}, voice {rep.snr_db:.0f} dB over room)"
                             f"<br>{tips_html(rep.tips)}")
            self.bridge.review.emit("<br><br>".join(lines))
        threading.Thread(target=work, daemon=True).start()

    def noise_for(self, path: Path):
        """Room tone of the slot that recorded this file (names carry MicA / MicB)."""
        for s in self.slots:
            if s.mic and s.title.replace(" ", "") in path.name:
                return voice.load_profile(s.mic.device)
        return None

    def open_editor(self, path: Path | None = None):
        from studio_utility.ui.editor import EditorWindow
        if self.editor is None:
            settings = lambda: self.voice.settings if self.voice else voice.VoiceSettings(enabled=False)
            self.editor = EditorWindow(settings, self.noise_for, self)
            self.editor.message.connect(self.message)
        if path is None:
            path, _ = QFileDialog.getOpenFileName(self, "Open recording", str(self.folder()), "Audio (*.wav *.flac *.aiff)")
            path = Path(path) if path else None
        if path:
            self.editor.load(path)
        self.editor.show()
        self.editor.raise_()

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
