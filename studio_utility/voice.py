"""Voice processing for external mics: an Audacity-style noise profile, a live effects chain
(pedalboard: low-cut, gate, EQ, compressor, limiter, optional VST3) and an offline "clean" pass.

Live (per audio block, low latency):  high-pass -> noise gate -> presence/air EQ -> compressor -> gain -> limiter
Offline (when a recording stops):      noise-profile spectral gating (noisereduce) -> the same chain, at full quality
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

try:
    import noisereduce as nr
    import pedalboard as pb
except Exception:  # optional: the Audio tab hides voice processing without them
    nr = pb = None

PROFILE_DIR = Path.home() / ".studio-utility" / "noise"
SETTINGS_FILE = Path.home() / ".studio-utility" / "voice.json"
RATE = 48000


@dataclass
class VoiceSettings:
    enabled: bool = True
    denoise: float = 0.85        # 0..1 share of profile noise removed in the saved _voice.wav
    low_cut_hz: float = 80       # rumble, handling noise, AC hum's low harmonics
    gate: bool = True            # mute below the learned noise floor (+6 dB) between phrases
    presence_db: float = 3.0     # +dB around 4 kHz: clarity / intelligibility
    air_db: float = 1.5          # +dB shelf above 10 kHz: openness
    compress: str = "medium"     # off / light / medium / strong
    gain_db: float = 0.0         # make-up gain after compression
    limit_db: float = -1.0       # brick-wall ceiling (no clipping)
    vst3: list[str] = field(default_factory=list)  # extra plugins, applied after the EQ

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


PRESETS = {
    "Clean voice": VoiceSettings(),
    "Podcast": VoiceSettings(denoise=0.9, low_cut_hz=90, presence_db=4, air_db=2, compress="strong", gain_db=3),
    "Outdoor / wind": VoiceSettings(denoise=0.95, low_cut_hz=150, presence_db=3, air_db=0, compress="medium"),
    "Natural (light)": VoiceSettings(denoise=0.6, low_cut_hz=60, gate=False, presence_db=1, air_db=0, compress="light"),
    "Off (raw)": VoiceSettings(enabled=False),
}
COMPRESSION = {"off": None, "light": (-16, 2.0), "medium": (-20, 3.0), "strong": (-24, 4.5)}


def load_settings() -> VoiceSettings:
    try:
        return VoiceSettings(**json.loads(SETTINGS_FILE.read_text()))
    except (FileNotFoundError, TypeError, json.JSONDecodeError):
        return VoiceSettings()


def save_settings(s: VoiceSettings) -> None:
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(s.to_json())


# ---- noise profile ("record 10 s of silence")
def profile_path(device: str) -> Path:
    safe = "".join(c if c.isalnum() else "_" for c in device)
    return PROFILE_DIR / f"{safe}.npy"


def save_profile(device: str, noise: np.ndarray) -> Path:
    """noise: (frames, channels) float32 room tone recorded with nobody talking."""
    p = profile_path(device)
    p.parent.mkdir(parents=True, exist_ok=True)
    np.save(p, noise.astype(np.float32))
    return p


def load_profile(device: str) -> np.ndarray | None:
    p = profile_path(device)
    return np.load(p) if p.exists() else None


def noise_floor_db(noise: np.ndarray | None) -> float:
    """RMS level of the profile, used to set the live gate threshold."""
    if noise is None or not len(noise):
        return -60.0
    return float(20 * np.log10(max(np.sqrt(np.mean(noise ** 2)), 1e-9)))


# ---- the chain
def build_chain(s: VoiceSettings, noise_db: float = -60.0):
    if pb is None or not s.enabled:
        return None
    fx = [pb.HighpassFilter(cutoff_frequency_hz=s.low_cut_hz)]
    if s.gate:
        fx.append(pb.NoiseGate(threshold_db=min(noise_db + 6, -20), ratio=10, attack_ms=2, release_ms=150))
    if s.presence_db:
        fx.append(pb.PeakFilter(cutoff_frequency_hz=4000, gain_db=s.presence_db, q=0.8))
    if s.air_db:
        fx.append(pb.HighShelfFilter(cutoff_frequency_hz=10000, gain_db=s.air_db))
    for path in s.vst3:
        fx.append(pb.load_plugin(path))
    comp = COMPRESSION.get(s.compress)
    if comp:
        fx.append(pb.Compressor(threshold_db=comp[0], ratio=comp[1], attack_ms=5, release_ms=80))
    if s.gain_db:
        fx.append(pb.Gain(gain_db=s.gain_db))
    fx.append(pb.Limiter(threshold_db=s.limit_db, release_ms=60))
    return pb.Pedalboard(fx)


class LiveVoice:
    """Stateful per-block processor for the audio callback (keeps filter/compressor state between blocks)."""

    def __init__(self, s: VoiceSettings, noise: np.ndarray | None = None, rate: int = RATE):
        self.rate = rate
        self.chain = build_chain(s, noise_floor_db(noise))

    def __call__(self, block: np.ndarray) -> np.ndarray:
        """block: (frames, channels) float32 -> same shape."""
        if self.chain is None:
            return block
        out = self.chain(block.T, self.rate, reset=False)
        return np.ascontiguousarray(out.T, dtype=np.float32)


def clean_file(src: Path, s: VoiceSettings, noise: np.ndarray | None, dst: Path | None = None) -> Path:
    """Offline best-quality pass for a finished recording: profile denoise, then the chain. Writes *_voice.wav."""
    import soundfile as sf
    audio, rate = sf.read(src, dtype="float32", always_2d=True)
    if s.enabled and nr is not None and s.denoise > 0:
        kw = {"y_noise": noise.T} if noise is not None else {}
        audio = nr.reduce_noise(y=audio.T, sr=rate, stationary=noise is not None, prop_decrease=s.denoise, **kw).T
    chain = build_chain(s, noise_floor_db(noise))
    if chain is not None:
        audio = chain(audio.T.astype(np.float32), rate).T
    dst = dst or src.with_name(src.stem + "_voice.wav")
    sf.write(dst, np.clip(audio, -1, 1), rate, subtype="PCM_24")
    return dst
