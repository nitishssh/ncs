"""Voice chain: noise-profile cleaning, live block processing, presets, output routing."""
from __future__ import annotations

import numpy as np
import pytest

from studio_utility import audio, voice

pytestmark = pytest.mark.skipif(voice.pb is None or voice.nr is None, reason="pedalboard/noisereduce not installed")
SR = 48000


def db(a):
    return 20 * np.log10(np.sqrt(np.mean(a ** 2)) + 1e-12)


@pytest.fixture
def take(tmp_path):
    """4 s of 220 Hz 'speech' bursts over -40 dB hiss, plus a 2 s noise-only profile."""
    import soundfile as sf
    rng = np.random.default_rng(0)
    t = np.arange(SR * 4) / SR
    speech = (0.3 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 0.75 * t) > 0))[:, None].repeat(2, 1)
    rec = (speech + rng.normal(0, 0.01, (len(t), 2))).astype(np.float32)
    path = tmp_path / "raw.wav"
    sf.write(path, rec, SR, subtype="PCM_24")
    return path, rec, t, rng.normal(0, 0.01, (SR * 2, 2)).astype(np.float32)


def test_clean_file_removes_background_keeps_speech(take):
    import soundfile as sf
    path, rec, t, noise = take
    out = voice.clean_file(path, voice.PRESETS["Clean voice"], noise)
    assert out.name == "raw_voice.wav"
    y, _ = sf.read(out, dtype="float32")
    phase = (0.75 * t) % 1.0
    gap, talk = (phase > 0.6) & (phase < 0.9), (phase > 0.1) & (phase < 0.4)
    assert db(rec[gap]) - db(y[gap]) > 30          # background between phrases essentially gone
    assert abs(db(rec[talk]) - db(y[talk])) < 4    # speech level preserved


def test_raw_preset_is_passthrough(take):
    import soundfile as sf
    path, rec, _, noise = take
    y, _ = sf.read(voice.clean_file(path, voice.PRESETS["Off (raw)"], noise), dtype="float32")
    assert np.allclose(y, rec, atol=1e-4)


def test_live_chain_keeps_shape_and_is_fast():
    import time
    live = voice.LiveVoice(voice.PRESETS["Podcast"])
    block = np.random.default_rng(1).normal(0, 0.05, (1024, 2)).astype(np.float32)
    t0 = time.perf_counter()
    for _ in range(50):
        out = live(block)
    assert out.shape == block.shape and out.dtype == np.float32
    assert (time.perf_counter() - t0) / 50 < 1024 / SR / 4  # well under a quarter of the real-time budget


def test_limiter_prevents_clipping():
    out = voice.LiveVoice(voice.VoiceSettings(gain_db=18, compress="off"))(np.full((1024, 2), 0.9, np.float32))
    assert np.abs(out).max() <= 1.0


def test_settings_and_profile_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "SETTINGS_FILE", tmp_path / "voice.json")
    monkeypatch.setattr(voice, "PROFILE_DIR", tmp_path / "noise")
    voice.save_settings(voice.PRESETS["Outdoor / wind"])
    assert voice.load_settings() == voice.PRESETS["Outdoor / wind"]
    noise = np.full((4800, 2), 0.01, np.float32)
    voice.save_profile("Wireless Mic Rx", noise)
    assert np.allclose(voice.load_profile("Wireless Mic Rx"), noise)
    assert round(voice.noise_floor_db(noise)) == -40


def test_output_route_drops_old_blocks(monkeypatch):
    class FakeOut:
        def __init__(self, **kw): pass
        def start(self): pass
        def stop(self): pass
        def close(self): pass
    monkeypatch.setattr(audio.sd, "OutputStream", FakeOut)
    monkeypatch.setattr(audio.sd, "query_devices", lambda d=None: {"max_output_channels": 2})
    r = audio.OutputRoute("BlackHole 16ch", max_blocks=4)
    for i in range(10):
        r.push(np.full((1024, 2), i, np.float32))
    assert len(r.q) == 4 and r.q[0][0, 0] == 6  # only the newest blocks: latency can't build up
    buf = np.empty((1024, 2), np.float32)
    r._callback(buf, 1024, None, None)
    assert buf[0, 0] == 6
