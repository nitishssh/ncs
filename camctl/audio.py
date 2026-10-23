"""External mics on the computer (DJI Mic receiver, Kreo Sonik, any USB/class-compliant input):
live per-channel levels and 48 kHz WAV recording, independent of the camera transport.

The mics' own settings (gain, noise cancelling, safety track) live in the vendors' apps/firmware and are
not reachable from here; this module only uses the standard audio input the receiver exposes."""
from __future__ import annotations

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


@dataclass
class InputDevice:
    name: str
    channels: int
    rate: int

    @property
    def label(self) -> str:
        brand = next((v for k, v in KNOWN.items() if k.lower() in self.name.lower()), None)
        return f"{brand} ({self.name})" if brand and brand != self.name else self.name


def input_devices() -> list[InputDevice]:
    if sd is None:
        return []
    return [InputDevice(d["name"], d["max_input_channels"], int(d["default_samplerate"]))
            for d in sd.query_devices() if d["max_input_channels"] > 0]


def dbfs(x: float) -> float:
    return 20 * np.log10(max(x, 1e-9))


class MicInput:
    """One input device: always-on metering, optional recording to WAV (24-bit, 48 kHz).

    levels(): latest (rms_dbfs, peak_dbfs) per channel; clipped: True if any sample hit ≥ -0.1 dBFS
    since the last clear_clip(). Recording writes on a background thread so the audio callback never blocks."""

    def __init__(self, device: str, channels: int = 2, rate: int = RATE):
        if sd is None:
            raise RuntimeError("audio support unavailable (pip install sounddevice soundfile)")
        self.device, self.channels, self.rate = device, channels, rate
        self._levels = [(-120.0, -120.0)] * channels
        self.clipped = False
        self._q: queue.Queue | None = None
        self._writer: threading.Thread | None = None
        self.path: Path | None = None
        self.started_at: float | None = None
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
            self._q.put(data.copy())

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

    def close(self):
        self.stop_recording()
        self.stream.stop()
        self.stream.close()
