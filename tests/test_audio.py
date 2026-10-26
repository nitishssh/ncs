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


def test_sources_are_told_apart():
    kinds = {n: audio.source_kind(n) for n in (
        "EOS R50 V", "Cam Link 4K", "USB Video", "MacBook Pro Microphone", "Microphone Array (Realtek(R) Audio)",
        "Wireless Mic Rx", "Kreo Sonik", "Scarlett 2i2 USB", "BlackHole 2ch", "CABLE Output (VB-Audio Virtual Cable)")}
    assert kinds == {"EOS R50 V": "camera", "Cam Link 4K": "camera", "USB Video": "camera",
                     "MacBook Pro Microphone": "computer", "Microphone Array (Realtek(R) Audio)": "computer",
                     "Wireless Mic Rx": "external", "Kreo Sonik": "external", "Scarlett 2i2 USB": "external",
                     "BlackHole 2ch": "virtual", "CABLE Output (VB-Audio Virtual Cable)": "virtual"}


def test_input_devices_grouped_camera_first(monkeypatch):
    if audio.sd is None:
        pytest.skip("sounddevice not installed")
    devs = [{"name": n, "max_input_channels": c, "max_output_channels": 0, "default_samplerate": 48000}
            for n, c in (("BlackHole 2ch", 2), ("Wireless Mic Rx", 2), ("MacBook Pro Microphone", 1), ("EOS R50 V", 2))]
    monkeypatch.setattr(audio.sd, "query_devices", lambda *a: devs)
    assert [d.kind for d in audio.input_devices()] == ["camera", "computer", "external", "virtual"]


def test_mono_laptop_mic_opens_mono(monkeypatch):
    if audio.sd is None:
        pytest.skip("sounddevice not installed")
    monkeypatch.setattr(audio.sd, "query_devices", lambda *a: {"max_input_channels": 1, "max_output_channels": 0})
    monkeypatch.setattr(audio.sd, "InputStream", lambda **kw: FakeStream(**kw))
    m = audio.MicInput("MacBook Pro Microphone")
    assert m.channels == 1
    m.stream.feed(0.1)
    assert len(m.levels()) == 1
    m.close()


def test_recent_keeps_a_rolling_window(mic):
    for _ in range(200):  # 200 x 4800 frames = 20 s, more than the window
        mic.fake.feed(0.1)
    assert len(mic.recent()) <= (audio.RECENT_S * audio.RATE // 1024 + 1) * 4800
    assert len(mic.recent()) > 0
