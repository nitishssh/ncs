"""Audio suggestions: listens to a few seconds of a mic (live) or a whole take (after it stops) and says, in plain
words, what is wrong and how to fix it - level, clipping, room noise, mains hum, rumble/wind, harsh "s" sounds,
and loudness against the -16 LUFS target used by YouTube/podcasts.

Only measurements, no machine learning: every tip names the number it came from, so it can be trusted or ignored."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import signal

TARGET_LUFS = -16.0
FRAME_S = 0.05


@dataclass
class Tip:
    level: str      # "problem" (fix before recording), "warn" (worth fixing), "ok"
    title: str
    fix: str

    def __str__(self) -> str:
        return f"{self.title} - {self.fix}" if self.fix else self.title


@dataclass
class Report:
    peak_db: float
    noise_db: float      # quietest 10% of 50 ms frames: the room between words
    speech_db: float     # loudest 20% of frames: the voice
    lufs: float | None   # integrated loudness (needs >= 0.4 s of sound)
    hum_hz: int | None
    tips: list[Tip]

    @property
    def snr_db(self) -> float:
        return self.speech_db - self.noise_db


def _db(x) -> np.ndarray:
    return 20 * np.log10(np.maximum(x, 1e-9))


def _k_weight(x: np.ndarray, rate: int) -> np.ndarray:
    """ITU-R BS.1770 K-weighting (high shelf +4 dB, then high-pass ~38 Hz), designed for any sample rate."""
    f0, g, q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
    k, vh = np.tan(np.pi * f0 / rate), 10 ** (g / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / q + k * k
    b1 = [(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0]
    a1 = [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    f0, q = 38.13547087602444, 0.5003270373238773
    k = np.tan(np.pi * f0 / rate)
    a0 = 1 + k / q + k * k
    b2, a2 = [1, -2, 1], [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0]
    return signal.lfilter(b2, a2, signal.lfilter(b1, a1, x, axis=0), axis=0)


def lufs(audio: np.ndarray, rate: int) -> float | None:
    """Integrated loudness, BS.1770 gating (400 ms blocks, 75% overlap, -70 LUFS absolute, -10 LU relative)."""
    x = audio if audio.ndim == 2 else audio[:, None]
    block, hop = int(0.4 * rate), int(0.1 * rate)
    if len(x) < block:
        return None
    y = _k_weight(x.astype(np.float64), rate)
    starts = range(0, len(y) - block + 1, hop)
    z = np.array([np.mean(y[s:s + block] ** 2, axis=0).sum() for s in starts])
    loud = -0.691 + 10 * np.log10(np.maximum(z, 1e-12))
    z = z[loud > -70]
    if not len(z):
        return None
    rel = -0.691 + 10 * np.log10(z.mean()) - 10
    z = z[-0.691 + 10 * np.log10(z) > rel]
    return float(-0.691 + 10 * np.log10(z.mean()))


def _band(freqs, power, lo, hi) -> float:
    m = (freqs >= lo) & (freqs < hi)
    return float(power[m].sum()) if m.any() else 0.0


def _hum(freqs, power) -> int | None:
    """50 or 60 Hz mains hum: the fundamental and its 2nd/3rd harmonics stand well above their neighbourhood."""
    best = None
    for base in (50, 60):
        score = []
        for h in (1, 2, 3):
            f = base * h
            near = (freqs > f - 2) & (freqs < f + 2)
            around = ((freqs > f - 12) & (freqs < f - 4)) | ((freqs > f + 4) & (freqs < f + 12))
            if near.any() and around.any():
                score.append(10 * np.log10(max(power[near].max(), 1e-20) / max(np.median(power[around]), 1e-20)))
        if len(score) >= 2 and sorted(score)[-2] > 12 and (best is None or max(score) > best[1]):
            best = (base, max(score))
    return best[0] if best else None


def analyze(audio: np.ndarray, rate: int, live: bool = False) -> Report:
    """audio: (frames,) or (frames, channels) float in -1..1. live=True words tips for "before you record"."""
    x = audio if audio.ndim == 2 else audio[:, None]
    mono = x.mean(axis=1)
    peak = float(_db(np.abs(x).max())) if len(x) else -120.0
    n = int(FRAME_S * rate)
    frames = mono[: len(mono) // n * n].reshape(-1, n) if len(mono) >= n else mono[None, :]
    rms = np.sort(_db(np.sqrt(np.mean(frames ** 2, axis=1))))
    noise = float(np.mean(rms[: max(1, len(rms) // 10)]))
    speech = float(np.mean(rms[-max(1, len(rms) // 5):]))
    loud = lufs(x, rate)

    freqs, power = signal.welch(mono, rate, nperseg=min(len(mono), 1 << 15)) if len(mono) > 256 else (np.zeros(1), np.zeros(1))
    voice_band = _band(freqs, power, 300, 4000)
    hum = _hum(freqs, power) if len(freqs) > 1 else None

    tips: list[Tip] = []
    sound = speech > -60
    if peak >= -0.5:
        tips.append(Tip("problem", f"Clipping (peak {peak:.1f} dBFS)",
                        "lower the gain on the mic/receiver or turn on its safety track; clipped audio can't be repaired"))
    elif sound and peak > -3:
        tips.append(Tip("warn", f"Very hot level (peak {peak:.1f} dBFS)",
                        "back the gain off ~3 dB so a laugh or shout doesn't clip"))
    if sound and speech < -30:
        tips.append(Tip("warn", f"Voice is quiet ({speech:.0f} dBFS)",
                        "move the mic closer (a hand's width below the chin for a lav) or raise the gain; "
                        "aim for speech around -18 to -12 dBFS"))
    if noise > -50:
        tips.append(Tip("problem" if noise > -40 else "warn", f"Noisy room ({noise:.0f} dBFS between words)",
                        "turn off fans/AC, close windows, then 'Learn noise' so it's removed from the take"))
    if sound and speech - noise < 20:
        tips.append(Tip("warn", f"Voice only {speech - noise:.0f} dB above the background",
                        "get the mic closer to the mouth - every halving of distance gains ~6 dB over the room"))
    if hum:
        tips.append(Tip("warn", f"{hum} Hz mains hum",
                        "plug the computer/receiver into a different socket, unplug chargers, or use a "
                        "battery-powered receiver; a ground-loop isolator fixes it for good"))
    df = float(freqs[1] - freqs[0]) if len(freqs) > 1 else 0.0
    rumble_db = 10 * np.log10(max(_band(freqs, power, 20, 90) * df, 1e-20))  # its level, in dBFS
    # only when it's actually loud: in a quiet room, room tone below 90 Hz always outweighs a voice that isn't there
    if voice_band and rumble_db > -50 and _band(freqs, power, 20, 90) > 0.15 * voice_band:
        tips.append(Tip("warn", "Strong rumble or wind below 90 Hz",
                        "use the foam/fur windscreen, turn on the camera's wind filter, raise Low cut to 100-150 Hz"))
    if sound and speech > -45 and voice_band and _band(freqs, power, 5000, 9000) > 0.35 * voice_band:
        tips.append(Tip("warn", "Harsh 's' sounds (sibilance)",
                        "angle the mic slightly off the mouth and lower Presence/Air by 2 dB"))
    if loud is not None and not live and sound and abs(loud - TARGET_LUFS) > 3:
        tips.append(Tip("ok", f"Loudness {loud:.0f} LUFS (target {TARGET_LUFS:.0f})",
                        f"{'raise' if loud < TARGET_LUFS else 'lower'} Gain by ~{abs(TARGET_LUFS - loud):.0f} dB "
                        "for YouTube/podcast level"))
    if not sound and not tips:
        tips.append(Tip("ok", "No voice heard yet", "say a few sentences at your normal level to get suggestions"))
    if not tips:
        tips.append(Tip("ok", "Sounds good", ""))
    order = {"problem": 0, "warn": 1, "ok": 2}
    tips.sort(key=lambda t: order[t.level])
    return Report(peak, noise, speech, loud, hum, tips)


def analyze_file(path) -> Report:
    import soundfile as sf
    audio, rate = sf.read(path, dtype="float32", always_2d=True)
    return analyze(audio, rate)
