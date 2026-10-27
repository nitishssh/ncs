"""Voice chain: noise-profile cleaning, live block processing, presets, output routing."""
from __future__ import annotations

import sys

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


@pytest.mark.skipif(audio.sd is None, reason="sounddevice/PortAudio not available")
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


# ---- phase 3: 10-band EQ, hum notch, AI denoise engines, loudness
import dataclasses  # noqa: E402

from studio_utility import advice  # noqa: E402

R = 48000
TT = np.arange(R * 2) / R


def _level(x, hz):
    f, p = __import__("scipy.signal", fromlist=["welch"]).welch(x[:, 0], R, nperseg=8192)
    return 10 * np.log10(p[np.argmin(abs(f - hz))])


def _run(s, x):
    return voice.build_chain(s)(x.T.astype(np.float32), R).T


needs_pb = pytest.mark.skipif(voice.pb is None, reason="pedalboard not installed")


@needs_pb
def test_eq_band_boosts_only_its_band():
    x = (0.05 * (np.sin(2 * np.pi * 1000 * TT) + np.sin(2 * np.pi * 125 * TT)))[:, None]
    flat = voice.VoiceSettings(gate=False, presence_db=0, air_db=0, compress="off", low_cut_hz=20)
    eq = [0.0] * 10
    eq[voice.EQ_BANDS.index(1000)] = 6
    y = _run(dataclasses.replace(flat, eq=eq), x)
    z = _run(flat, x)
    assert _level(y, 1000) - _level(z, 1000) == pytest.approx(6, abs=1)
    assert abs(_level(y, 125) - _level(z, 125)) < 1


@needs_pb
def test_hum_notch_removes_mains():
    x = (0.1 * np.sin(2 * np.pi * 50 * TT) + 0.05 * np.sin(2 * np.pi * 1000 * TT))[:, None]
    base = dict(gate=False, presence_db=0, air_db=0, compress="off", low_cut_hz=20)
    off, on = _run(voice.VoiceSettings(**base), x), _run(voice.VoiceSettings(hum="50", **base), x)
    assert _level(off, 50) - _level(on, 50) > 20
    assert abs(_level(off, 1000) - _level(on, 1000)) < 1


@pytest.mark.skipif(voice.rnnoise is None, reason="pyrnnoise not installed")
def test_rnnoise_streams_any_block_size():
    r = voice.Rnnoise(2)
    noise = np.random.default_rng(0).normal(0, 0.05, (R, 2)).astype(np.float32)
    out = np.concatenate([r(noise[i:i + 1024]) for i in range(0, R - R % 1024, 1024)])
    r.close()
    assert out.shape == (R - R % 1024, 2)
    assert np.sqrt(np.mean(out[R // 2:] ** 2)) < 0.5 * 0.05  # steady hiss is pushed well down


def test_denoise_falls_back_when_engine_missing(monkeypatch):
    monkeypatch.setattr(voice, "engines", lambda: ["profile"] if voice.nr is not None else [])
    x = np.random.default_rng(1).normal(0, 0.01, (R, 1)).astype(np.float32)
    _, used = voice.denoise(x, R, voice.VoiceSettings(engine="deepfilternet"), None)
    assert used == ("profile" if voice.nr is not None else "none")


@needs_pb
def test_clean_file_hits_loudness_target(tmp_path):
    sf = pytest.importorskip("soundfile")
    src = tmp_path / "take.wav"
    sf.write(src, (0.02 * np.sin(2 * np.pi * 440 * np.arange(R * 3) / R))[:, None], R, subtype="PCM_24")
    s = voice.VoiceSettings(denoise=0, gate=False, compress="off", presence_db=0, air_db=0, target_lufs=-16)
    out, rate = sf.read(voice.clean_file(src, s, None), always_2d=True)
    assert advice.lufs(out, rate) == pytest.approx(-16, abs=0.5)


def test_old_settings_files_still_load(tmp_path, monkeypatch):
    monkeypatch.setattr(voice, "SETTINGS_FILE", tmp_path / "voice.json")
    (tmp_path / "voice.json").write_text('{"enabled": true, "denoise": 0.5, "compress": "light"}')
    s = voice.load_settings()
    assert s.denoise == 0.5 and s.eq == [0.0] * 10 and s.engine == "profile" and s.normalize


def test_peak_limit_never_exceeds_ceiling_or_adds_gain():
    x = np.random.default_rng(4).normal(0, 0.4, (R, 2))
    y = voice.peak_limit(x, R, -1.0)
    assert np.abs(y).max() <= 10 ** (-1 / 20) + 1e-9
    assert np.all(np.abs(y) <= np.abs(x) + 1e-12)


@pytest.mark.skipif(voice.rnnoise is None, reason="pyrnnoise not installed")
def test_rnnoise_cleaned_take_stays_in_sync():
    from scipy import signal as sg
    x = (np.random.default_rng(5).normal(0, 0.1, (R * 2, 1)) * (np.sin(2 * np.pi * 3 * np.arange(R * 2) / R) > 0)[:, None])
    y, used = voice.denoise(x.astype(np.float32), R, voice.VoiceSettings(engine="rnnoise", denoise=1.0), None)
    c = sg.correlate(y[:, 0], x[:, 0], mode="full", method="fft")
    assert used == "rnnoise" and len(y) == len(x) and abs(np.argmax(c) - (len(x) - 1)) <= 2


# ---- DeepFilterNet runs as a separate program; a stand-in script checks how it's called
FAKE_DF = """#!{python}
import sys, soundfile as sf
from pathlib import Path
args = sys.argv[1:]
out = Path(args[args.index("--output-dir") + 1]); out.mkdir(parents=True, exist_ok=True)
Path(out.parent / "args.txt").write_text(" ".join(args))
src = Path(args[-1])
y, r = sf.read(src, always_2d=True)
assert sf.info(src).subtype == "FLOAT"            # the real one can't read 24-bit
sf.write(out / src.name, y[:-100] * 0.5, r, subtype="PCM_16")  # real output is 16-bit and a bit short
"""


@pytest.fixture
def fake_df(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("shebang script")
    exe = tmp_path / "deep-filter"
    exe.write_text(FAKE_DF.format(python=sys.executable))
    exe.chmod(0o755)
    monkeypatch.setenv("STUDIO_UTILITY_DEEPFILTER", str(exe))
    monkeypatch.setattr(voice.tempfile, "TemporaryDirectory", lambda prefix="": _Keep(tmp_path / "work"))
    return tmp_path


class _Keep:
    def __init__(self, p):
        self.p = p

    def __enter__(self):
        self.p.mkdir(exist_ok=True)
        return str(self.p)

    def __exit__(self, *a):
        return False


def test_deepfilternet_called_as_a_program(fake_df):
    assert "deepfilternet" in voice.engines()
    x = np.full((R, 2), 0.2, np.float32)
    y, used = voice.denoise(x, R, voice.VoiceSettings(engine="deepfilternet", denoise=0.9), None)
    assert used == "deepfilternet" and y.shape == x.shape and y.dtype == np.float32
    assert np.allclose(y[:R - 100], 0.1, atol=1e-3) and not y[R - 100:].any()  # padded back to full length
    args = (fake_df / "work" / "args.txt").read_text().split()
    assert "--compensate-delay" in args and args[args.index("--atten-lim-db") + 1] == "20.0"  # 0.9 -> 20 dB


def test_deepfilternet_install_rejects_a_bad_download(tmp_path, monkeypatch):
    import httpx
    monkeypatch.setattr(voice, "BIN_DIR", tmp_path)
    monkeypatch.setattr(voice, "_df_build", lambda: ("deep-filter-x", "0" * 64))

    class Resp:
        headers = {"content-length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def raise_for_status(self):
            pass

        def iter_bytes(self, n):
            yield b"evil"
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: Resp())
    with pytest.raises(RuntimeError, match="checksum"):
        voice.install_deepfilter()
    assert list(tmp_path.iterdir()) == []  # nothing left behind, nothing runnable


def test_deepfilternet_install_pins_every_desktop_build():
    for (plat, arch), (name, sha) in voice.DF_BUILDS.items():
        assert voice.DF_VERSION in name and len(sha) == 64
    assert {("darwin", "arm64"), ("darwin", "x86_64"), ("win32", "amd64")} <= set(voice.DF_BUILDS)


def test_normalize_leaves_near_silence_alone_and_caps_the_boost():
    rng = np.random.default_rng(7)
    hiss = (0.0005 * rng.normal(size=(R * 3, 1))).astype(np.float32)          # ~ -66 dBFS room tone
    assert np.array_equal(voice.normalize(hiss, R, -16), hiss)
    quiet = (0.006 * np.sin(2 * np.pi * 440 * np.arange(R * 3) / R))[:, None]  # ~ -47 LUFS: needs +31 dB
    before, after = advice.lufs(quiet, R), advice.lufs(voice.normalize(quiet, R, -16), R)
    assert after - before == pytest.approx(voice.MAX_BOOST_DB, abs=0.5)
