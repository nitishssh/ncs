"""External mics on the computer (DJI Mic receiver, Kreo Sonik, any USB/class-compliant input):
live per-channel levels and 48 kHz WAV recording, independent of the camera transport.

The mics' own settings (gain, noise cancelling, safety track) live in the vendors' apps/firmware and are
not reachable from here; this module only uses the standard audio input the receiver exposes."""
from __future__ import annotations

import collections
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

try:
    import sounddevice as sd
    import soundfile as sf
except Exception:  # PortAudio missing: the UI shows the Audio tab as unavailable
    sd = sf = None

KNOWN = {"Wireless Mic Rx": "DJI Mic", "Kreo Sonik": "Kreo Sonik"}
RATE = 48000
RECENT_S = 8  # rolling window kept for live suggestions

# Where a sound comes from, judged by the name the OS gives the input:
#  camera   - the R50 V itself over USB (UVC/UAC streaming) or an HDMI capture card carrying its audio
#  computer - the Mac's or PC's own microphone
#  external - receivers and USB mics (DJI Mic, Kreo Sonik, interfaces…)
#  virtual  - loopback devices (BlackHole, VB-Cable): other apps' sound, not a mic
SOURCES = ("camera", "computer", "external", "virtual")
SOURCE_LABELS = {"camera": "Camera", "computer": "This computer", "external": "External mic",
                 "virtual": "Virtual", "phone": "Phone"}
_CAMERA = ("eos", "canon", "cam link", "hdmi", "capture", "usb video", "uvc", "elgato", "game capture")
_COMPUTER = ("macbook", "imac", "mac mini", "mac studio", "built-in", "internal", "microphone array",
             "realtek", "intel smart sound", "conexant", "iphone microphone")
_VIRTUAL = ("blackhole", "loopback", "vb-audio", "cable output", "soundflower", "stereo mix", "zoomaudio",
            "teams audio", "aggregate", "multi-output")


def source_kind(name: str) -> str:
    n = name.lower()
    if any(k.lower() in n for k in KNOWN):
        return "external"
    for kind, words in (("virtual", _VIRTUAL), ("camera", _CAMERA), ("computer", _COMPUTER)):
        if any(w in n for w in words):
            return kind
    return "external"


@dataclass
class InputDevice:
    name: str
    channels: int
    rate: int

    @property
    def kind(self) -> str:
        return source_kind(self.name)

    @property
    def label(self) -> str:
        brand = next((v for k, v in KNOWN.items() if k.lower() in self.name.lower()), None)
        return f"{brand} ({self.name})" if brand and brand != self.name else self.name


def output_devices() -> list[str]:
    """Where processed voice can go: headphones, a receiver's output (-> 3.5 mm into the camera's MIC IN),
    or a virtual device like BlackHole (-> OBS, Zoom, any app as a system mic)."""
    if sd is None:
        return []
    return [d["name"] for d in sd.query_devices() if d["max_output_channels"] > 0]


class OutputRoute:
    """Plays processed blocks on an output device. A short queue decouples it from the mic, so a slow or
    unplugged output can never stall recording; if it falls behind, old blocks are dropped (latency stays low)."""

    def __init__(self, device: str, channels: int = 2, rate: int = RATE, max_blocks: int = 4):
        self.device, self.max_blocks = device, max_blocks
        self.q: collections.deque = collections.deque()
        dev_ch = max(1, min(channels, sd.query_devices(device)["max_output_channels"]))
        self.channels = dev_ch
        self.stream = sd.OutputStream(device=device, channels=dev_ch, samplerate=rate, dtype="float32",
                                      blocksize=1024, callback=self._callback)
        self.stream.start()

    def push(self, block: np.ndarray):
        self.q.append(block[:, :self.channels] if block.shape[1] >= self.channels else
                      np.repeat(block[:, :1], self.channels, axis=1))
        while len(self.q) > self.max_blocks:
            self.q.popleft()

    def _callback(self, out, frames, t, status):
        if self.q and len(self.q[0]) == frames:
            out[:] = self.q.popleft()
        else:
            out.fill(0)

    def close(self):
        self.stream.stop()
        self.stream.close()


def input_devices() -> list[InputDevice]:
    """Every input, grouped camera -> computer -> external -> virtual (stable order inside a group)."""
    if sd is None:
        return []
    found = [InputDevice(d["name"], d["max_input_channels"], int(d["default_samplerate"]))
             for d in sd.query_devices() if d["max_input_channels"] > 0]
    return sorted(found, key=lambda d: SOURCES.index(d.kind))


def dbfs(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


class MicInput:
    """One input device: always-on metering, optional recording to WAV (24-bit, 48 kHz).

    levels(): latest (rms_dbfs, peak_dbfs) per channel; clipped: True if any sample hit ≥ -0.1 dBFS
    since the last clear_clip(). Recording writes on a background thread so the audio callback never blocks."""

    def __init__(self, device: str, channels: int = 2, rate: int = RATE):
        if sd is None:
            raise RuntimeError("audio support unavailable (pip install sounddevice soundfile)")
        try:  # built-in laptop mics are mono: ask for what the device has (max 2)
            channels = max(1, min(channels, sd.query_devices(device)["max_input_channels"]))
        except Exception:
            pass
        self.device, self.channels, self.rate = device, channels, rate
        self._levels = [(-120.0, -120.0)] * channels
        self.clipped = False
        self._q: queue.Queue | None = None
        self._writer: threading.Thread | None = None
        self.path: Path | None = None
        self.started_at: float | None = None
        self.process = None              # callable(block) -> block: the live voice chain
        self.outputs: list[OutputRoute] = []
        self._capture: list | None = None  # blocks collected by capture() (noise profile)
        self._recent: collections.deque = collections.deque(maxlen=int(RECENT_S * rate / 1024) + 1)
        self.stream = sd.InputStream(device=device, channels=channels, samplerate=rate, dtype="float32",
                                     blocksize=1024, callback=self._callback)
        self.stream.start()

    def _callback(self, data, frames, t, status):
        rms = np.sqrt(np.mean(data ** 2, axis=0))
        peak = np.max(np.abs(data), axis=0)
        self._levels = [(dbfs(r), dbfs(p)) for r, p in zip(rms, peak)]
        if peak.max() >= 0.989:  # -0.1 dBFS
            self.clipped = True
        if self._q is not None:
            self._q.put(data.copy())     # recordings stay raw; the cleaned copy is made offline
        if self._capture is not None:
            self._capture.append(data.copy())
        self._recent.append(data.copy())
        if self.outputs:
            out = data
            if self.process is not None:
                try:
                    out = self.process(data)
                except Exception:
                    self.process = None  # a broken plugin must not kill the audio stream
            for route in self.outputs:
                route.push(out.copy())

    def recent(self) -> np.ndarray:
        """The last RECENT_S seconds of input (raw), for live suggestions."""
        blocks = list(self._recent)
        return np.concatenate(blocks) if blocks else np.zeros((0, self.channels), np.float32)

    def levels(self) -> list[tuple[float, float]]:
        return list(self._levels)

    def clear_clip(self):
        self.clipped = False

    @property
    def recording(self) -> bool:
        return self._q is not None

    def start_recording(self, path: Path) -> Path:
        if self.recording:
            return self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.started_at = path, time.time()
        q = self._q = queue.Queue()

        def write():
            with sf.SoundFile(path, "w", samplerate=self.rate, channels=self.channels, subtype="PCM_24") as f:
                while True:
                    block = q.get()
                    if block is None:
                        return
                    f.write(block)
        self._writer = threading.Thread(target=write, daemon=True)
        self._writer.start()
        return path

    def stop_recording(self) -> Path | None:
        if not self.recording:
            return None
        q, self._q = self._q, None
        q.put(None)
        self._writer.join(5)
        return self.path

    def capture(self, seconds: float) -> np.ndarray:
        """Collect `seconds` of input (e.g. room tone for a noise profile). Blocks the caller."""
        self._capture = []
        time.sleep(seconds)
        blocks, self._capture = self._capture, None
        return np.concatenate(blocks) if blocks else np.zeros((0, self.channels), np.float32)

    def set_outputs(self, devices: list[str]):
        for r in self.outputs:
            r.close()
        self.outputs = []
        for d in devices:
            try:
                self.outputs.append(OutputRoute(d, self.channels, self.rate))
            except Exception:
                pass  # device vanished / busy: skip it, keep the rest

    def close(self):
        self.set_outputs([])
        self.stop_recording()
        self.stream.stop()
        self.stream.close()
