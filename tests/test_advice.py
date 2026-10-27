"""Audio suggestions on synthetic takes: each problem is planted and must be named (and nothing else invented)."""
from __future__ import annotations

import numpy as np
import pytest
from scipy import signal

from studio_utility import advice

R = 48000
T = np.arange(R * 5) / R


def voice(amp=0.2):
    """Band-limited noise bursts (300 Hz-3 kHz) switching on and off like phrases."""
    b, a = signal.butter(4, [300, 3000], "band", fs=R)
    return signal.lfilter(b, a, np.random.default_rng(0).normal(0, amp, len(T))) * (np.sin(2 * np.pi * 1.5 * T) > 0)


def titles(x, **kw):
    return " | ".join(t.title for t in advice.analyze(x, R, **kw).tips)


def test_lufs_matches_bs1770_reference():
    assert advice.lufs(np.sin(2 * np.pi * 997 * T), R) == pytest.approx(-3.01, abs=0.05)  # 0 dBFS sine, mono


def test_clean_take_only_mentions_loudness():
    tips = advice.analyze(voice() + 1e-4 * np.random.default_rng(1).normal(size=len(T)), R).tips
    assert [t.title.split(" ")[0] for t in tips] == ["Loudness"]


def test_clipping_is_a_problem_first():
    tips = advice.analyze(np.clip(voice() * 8, -1, 1), R).tips
    assert tips[0].level == "problem" and tips[0].title.startswith("Clipping")


def test_noisy_room_and_poor_separation():
    t = titles(voice() + 0.02 * np.random.default_rng(2).normal(size=len(T)))
    assert "Noisy room" in t and "above the background" in t


@pytest.mark.parametrize("mains", [50, 60])
def test_mains_hum(mains):
    hum = sum(a * np.sin(2 * np.pi * mains * h * T) for h, a in ((1, 0.01), (2, 0.005), (3, 0.004)))
    rep = advice.analyze(voice() + hum, R)
    assert rep.hum_hz == mains


def test_wind_rumble():
    b, a = signal.butter(4, 60, "low", fs=R)
    wind = signal.lfilter(b, a, np.random.default_rng(3).normal(0, 0.5, len(T)))
    assert "rumble or wind" in titles(voice() + wind)


def test_silence_asks_for_speech_live():
    assert advice.analyze(np.zeros(R * 2), R, live=True).tips[0].title == "No voice heard yet"


def test_quiet_room_raises_no_rumble_or_sibilance():
    """Seen live on a Kreo Sonik at -58 dBFS: faint room tone must not be called wind."""
    b, a = signal.butter(2, 120, "low", fs=R)
    room = signal.lfilter(b, a, np.random.default_rng(6).normal(0, 0.003, len(T)))
    t = titles(room, live=True)
    assert "rumble" not in t and "sibilance" not in t
