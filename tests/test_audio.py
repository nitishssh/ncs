"""Audio engine with a fake PortAudio stream (no real mic needed)."""
from __future__ import annotations

import numpy as np
import pytest

from studio_utility import audio


class FakeStream:
    def __init__(self, callback, channels, **kw):
        self.cb, self.ch = callback, channels

    def start(self):
        pass

    def stop(self):
        pass

    def close(self):
        pass

    def feed(self, amp: float, frames: int = 4800):
        self.cb(np.full((frames, self.ch), amp, np.float32), frames, None, None)


@pytest.fixture
def mic(monkeypatch):
    if audio.sd is None:
        pytest.skip("sounddevice not installed")
    made = []
    monkeypatch.setattr(audio.sd, "InputStream", lambda **kw: made.append(FakeStream(**kw)) or made[-1])
    m = audio.MicInput("Kreo Sonik")
    m.fake = made[0]
    yield m
    m.close()


def test_levels_and_clip(mic):
    mic.fake.feed(0.1)
    (rms, peak), _ = mic.levels()
    assert round(peak) == -20 and not mic.clipped
    mic.fake.feed(1.0)
    assert mic.clipped
    mic.clear_clip()
    assert not mic.clipped


def test_records_24bit_wav(mic, tmp_path):
    path = mic.start_recording(tmp_path / "take.wav")
    for _ in range(10):
        mic.fake.feed(0.25)  # 10 x 4800 frames = 1.0 s at 48 kHz
    mic.stop_recording()
    info = audio.sf.info(path)
    assert (info.samplerate, info.channels, info.subtype) == (48000, 2, "PCM_24")
    assert info.frames == 48000


def test_device_labels():
    assert audio.InputDevice("Wireless Mic Rx", 2, 48000).label == "DJI Mic (Wireless Mic Rx)"
    assert audio.InputDevice("Kreo Sonik", 2, 48000).label == "Kreo Sonik"


def test_meter_colours():
    from studio_utility.ui.audio_tab import GREEN, RED, YELLOW, meter_style
    assert [meter_style(x) for x in (-30, -6, -1)] == [GREEN, YELLOW, RED]
