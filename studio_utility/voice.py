"""Voice processing for external mics: an Audacity-style noise profile, a live effects chain
(pedalboard: low-cut, hum notch, gate, 10-band EQ, compressor, limiter, optional VST3) and an offline "clean" pass.

Live (per audio block, low latency):  [RNNoise] -> high-pass -> hum notch -> gate -> presence/air + 10-band EQ
                                      -> compressor -> gain -> limiter
Offline (when a recording stops):      denoise (noise profile, RNNoise or DeepFilterNet) -> the same chain
                                      -> loudness normalised to the target LUFS (YouTube/podcast -16)

Denoise engines, best first (all open source; the ones not installed are skipped):
  deepfilternet - DeepFilterNet 3, a speech-enhancement network: removes changing noise (traffic, fans, keyboard,
                  crowd) that a profile can't. Offline only. Runs as its own process: the project's standalone
                  Rust build `deep-filter` (model built in, no Python/PyTorch), because the Python package pins
                  numpy < 2. "Get DeepFilterNet" in the Audio tab downloads it (install_deepfilter()).
  rnnoise       - Xiph RNNoise: small recurrent network, 10 ms frames, light enough for live use. `pip install pyrnnoise`.
  profile       - noisereduce spectral gating with the learned room tone (always available): best for steady hiss/hum.
"""
from __future__ import annotations

import json
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

try:
    import noisereduce as nr
    import pedalboard as pb
except Exception:  # optional: the Audio tab hides voice processing without them
    nr = pb = None
try:
    from pyrnnoise import rnnoise
except Exception:
    rnnoise = None

PROFILE_DIR = Path.home() / ".studio-utility" / "noise"
BIN_DIR = Path.home() / ".studio-utility" / "bin"
SETTINGS_FILE = Path.home() / ".studio-utility" / "voice.json"
RATE = 48000


EQ_BANDS = (31, 62, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)  # Hz, the classic 10-band graphic EQ
EQ_PRESETS = {
    "Flat": [0] * 10,
    "Voice clarity": [-6, -4, -2, -1, 0, 1, 2, 3, 2, 1],
    "Warm": [0, 2, 2, 1, 0, -1, -1, 0, 0, -1],
    "Thin out boom": [-8, -6, -4, -2, -1, 0, 0, 1, 0, 0],
    "De-harsh": [0, 0, 0, 0, 0, 0, -1, -2, -3, -2],
    "Radio / phone": [-24, -24, -12, 0, 2, 3, 3, 0, -12, -24],
}
ENGINES = {"profile": "Noise profile", "rnnoise": "RNNoise (AI, light)", "deepfilternet": "DeepFilterNet (AI, best)"}


def engines() -> list[str]:
    """Denoise engines usable on this computer, best first."""
    out = []
    if deepfilter_exe():
        out.append("deepfilternet")
    if rnnoise is not None:
        out.append("rnnoise")
    if nr is not None:
        out.append("profile")
    return out


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
    eq: list[float] = field(default_factory=lambda: [0.0] * len(EQ_BANDS))  # dB per EQ_BANDS
    hum: str = "off"             # off / 50 / 60: notch the mains frequency and two harmonics
    engine: str = "profile"      # offline denoise engine (see ENGINES); falls back when not installed
    live_denoise: bool = False   # RNNoise on the live outputs (adds 30 ms)
    normalize: bool = True       # bring the saved _voice.wav to target_lufs
    target_lufs: float = -16.0

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
    if s.hum in ("50", "60"):
        fx += [pb.PeakFilter(cutoff_frequency_hz=int(s.hum) * h, gain_db=-30, q=30) for h in (1, 2, 3)]
    if s.gate:
        fx.append(pb.NoiseGate(threshold_db=min(noise_db + 6, -20), ratio=10, attack_ms=2, release_ms=150))
    if s.presence_db:
        fx.append(pb.PeakFilter(cutoff_frequency_hz=4000, gain_db=s.presence_db, q=0.8))
    if s.air_db:
        fx.append(pb.HighShelfFilter(cutoff_frequency_hz=10000, gain_db=s.air_db))
    for i, (hz, db) in enumerate(zip(EQ_BANDS, s.eq)):
        if not db:
            continue
        if i == 0:
            fx.append(pb.LowShelfFilter(cutoff_frequency_hz=hz * 1.4, gain_db=db))
        elif i == len(EQ_BANDS) - 1:
            fx.append(pb.HighShelfFilter(cutoff_frequency_hz=hz * 0.7, gain_db=db))
        else:
            fx.append(pb.PeakFilter(cutoff_frequency_hz=hz, gain_db=db, q=1.4))
    for path in s.vst3:
        fx.append(pb.load_plugin(path))
    comp = COMPRESSION.get(s.compress)
    if comp:
        fx.append(pb.Compressor(threshold_db=comp[0], ratio=comp[1], attack_ms=5, release_ms=80))
    if s.gain_db:
        fx.append(pb.Gain(gain_db=s.gain_db))
    fx.append(pb.Limiter(threshold_db=s.limit_db, release_ms=60))
    return pb.Pedalboard(fx)


class Rnnoise:
    """Streaming RNNoise for any block size: buffers to its 480-sample (10 ms) frames, one state per channel.
    Output is delayed by one frame so every block can be answered in full."""
    FRAME = 480
    DELAY = 3 * FRAME  # total output delay, measured: one frame of buffering here + RNNoise's own 20 ms

    def __init__(self, channels: int):
        self.states = [rnnoise.create() for _ in range(channels)]
        self.pending = np.zeros((0, channels), np.float32)
        self.ready = np.zeros((self.FRAME, channels), np.float32)

    def __call__(self, block: np.ndarray) -> np.ndarray:
        self.pending = np.concatenate([self.pending, block])
        n = len(self.pending) // self.FRAME * self.FRAME
        if n:
            frames = self.pending[:n].reshape(-1, self.FRAME, block.shape[1])
            done = [np.stack([rnnoise.process_mono_frame(st, np.clip(f[:, c], -1, 1))[0]
                              for c, st in enumerate(self.states)], axis=1) for f in frames]
            self.ready = np.concatenate([self.ready, np.concatenate(done).astype(np.float32) / 32767])
            self.pending = self.pending[n:]
        out, self.ready = self.ready[:len(block)], self.ready[len(block):]
        return out

    def close(self):
        for st in self.states:
            rnnoise.destroy(st)
        self.states = []


class LiveVoice:
    """Stateful per-block processor for the audio callback (keeps filter/compressor state between blocks)."""

    def __init__(self, s: VoiceSettings, noise: np.ndarray | None = None, rate: int = RATE):
        self.rate = rate
        self.chain = build_chain(s, noise_floor_db(noise))
        self.denoise = None
        self._want_rnnoise = s.enabled and s.live_denoise and rnnoise is not None and rate == 48000

    def __call__(self, block: np.ndarray) -> np.ndarray:
        """block: (frames, channels) float32 -> same shape."""
        if self._want_rnnoise:
            if self.denoise is None or len(self.denoise.states) != block.shape[1]:
                self.denoise = Rnnoise(block.shape[1])
            block = self.denoise(block)
        if self.chain is None:
            return block
        out = self.chain(block.T, self.rate, reset=False)
        return np.ascontiguousarray(out.T, dtype=np.float32)


# ---- DeepFilterNet (standalone Rust build, run as a subprocess)
DF_VERSION = "0.5.6"
DF_URL = "https://github.com/Rikorose/DeepFilterNet/releases/download/v{v}/{name}"
# Official release assets; GitHub publishes no checksums for this release, so they are pinned here (2026-10-26)
DF_BUILDS = {
    ("darwin", "arm64"): ("deep-filter-0.5.6-aarch64-apple-darwin",
                          "4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081"),
    ("darwin", "x86_64"): ("deep-filter-0.5.6-x86_64-apple-darwin",
                           "d3be84003acb7c23e738ad7f70a158ec779a8d233a82e7fa3e717d112eb5b50f"),
    ("win32", "amd64"): ("deep-filter-0.5.6-x86_64-pc-windows-msvc.exe",
                         "75e11fa16445f560cb6b021521ddb89e89270d13b83089705d98776f58fd7915"),
}


def _df_build() -> tuple[str, str] | None:
    return DF_BUILDS.get((sys.platform, platform.machine().lower()))


def deepfilter_exe() -> str | None:
    """STUDIO_UTILITY_DEEPFILTER, then the copy install_deepfilter() made, then `deep-filter` on PATH."""
    env = os.environ.get("STUDIO_UTILITY_DEEPFILTER")
    if env and Path(env).is_file():
        return env
    exe = BIN_DIR / ("deep-filter.exe" if sys.platform == "win32" else "deep-filter")
    if exe.is_file():
        return str(exe)
    return shutil.which("deep-filter")


def can_install_deepfilter() -> bool:
    return _df_build() is not None


def install_deepfilter(progress=None) -> Path:
    """Download the official deep-filter build for this computer (~28 MB), check its pinned SHA-256, install it
    into BIN_DIR. progress(done_bytes, total_bytes) is called while downloading."""
    import httpx
    build = _df_build()
    if build is None:
        raise RuntimeError(f"no DeepFilterNet build for {sys.platform}/{platform.machine()}")
    name, sha = build
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    exe = BIN_DIR / ("deep-filter.exe" if sys.platform == "win32" else "deep-filter")
    part = exe.with_suffix(".part")
    digest = hashlib.sha256()
    with httpx.stream("GET", DF_URL.format(v=DF_VERSION, name=name), follow_redirects=True, timeout=60) as r:
        r.raise_for_status()
        total, done = int(r.headers.get("content-length", 0)), 0
        with part.open("wb") as f:
            for chunk in r.iter_bytes(1 << 16):
                f.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if digest.hexdigest() != sha:
        part.unlink()
        raise RuntimeError("DeepFilterNet download didn't match its checksum - not installed")
    part.chmod(0o755)
    part.replace(exe)
    return exe


def _deepfilternet(audio: np.ndarray, rate: int, amount: float) -> np.ndarray:
    """deep-filter on a temp copy. It can't read 24-bit WAV, so it gets 32-bit float; it writes 16-bit.
    amount (the Noise removal slider) maps to its attenuation limit: 0.85 -> at most 16.5 dB less noise, 1 -> no limit."""
    import soundfile as sf
    limit = 100 if amount >= 0.999 else -20 * np.log10(max(1 - amount, 1e-5))
    with tempfile.TemporaryDirectory(prefix="studio-df-") as tmp:
        src = Path(tmp) / "take.wav"
        sf.write(src, audio, rate, subtype="FLOAT")
        p = subprocess.run([deepfilter_exe(), "--compensate-delay", "--atten-lim-db", f"{limit:.1f}",
                            "--output-dir", str(Path(tmp) / "out"), str(src)],
                           capture_output=True, text=True, timeout=600,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if p.returncode:
            raise RuntimeError(f"DeepFilterNet failed: {(p.stderr or p.stdout).strip()[-300:]}")
        y, _ = sf.read(Path(tmp) / "out" / "take.wav", dtype="float32", always_2d=True)
    if len(y) < len(audio):  # its output is a few ms short at the end
        y = np.concatenate([y, np.zeros((len(audio) - len(y), y.shape[1]), np.float32)])
    return y[:len(audio)]


def denoise(audio: np.ndarray, rate: int, s: VoiceSettings, noise: np.ndarray | None) -> tuple[np.ndarray, str]:
    """Run the chosen engine, falling back down the list when one isn't installed. Returns (audio, engine used)."""
    order = [s.engine] + [e for e in ("deepfilternet", "rnnoise", "profile") if e != s.engine]
    for engine in (e for e in order if e in engines()):
        if engine == "deepfilternet":
            return _deepfilternet(audio, rate, s.denoise), engine
        if engine == "rnnoise" and rate == 48000:
            r = Rnnoise(audio.shape[1])
            y = np.concatenate([r(audio), r(np.zeros((Rnnoise.DELAY, audio.shape[1]), np.float32))])
            r.close()
            y = y[Rnnoise.DELAY:]  # undo the delay, so the cleaned take stays in sync with the camera
            return s.denoise * y + (1 - s.denoise) * audio, engine
        if engine == "profile":
            kw = {"y_noise": noise.T} if noise is not None else {}
            return nr.reduce_noise(y=audio.T, sr=rate, stationary=noise is not None, prop_decrease=s.denoise,
                                   **kw).T, engine
    return audio, "none"


MAX_BOOST_DB = 20.0  # loudness never adds more: a take that needs more has no usable voice, only noise to raise
SILENT_LUFS = -50.0  # quieter than this is silence / room tone: left as it is


def normalize(audio: np.ndarray, rate: int, target_lufs: float, ceiling_db: float = -1.0,
              max_boost_db: float = MAX_BOOST_DB) -> np.ndarray:
    """Gain to target_lufs (at most +max_boost_db), then a limiter so loud peaks stay under the ceiling.
    A near-silent take is left alone: boosting it 40 dB only turned leftover noise into a 'peak -1 dBFS' file."""
    from studio_utility.advice import lufs
    now = lufs(audio, rate)
    if now is None or now < SILENT_LUFS:
        return audio
    out = audio * 10 ** (min(target_lufs - now, max_boost_db) / 20)
    return peak_limit(out, rate, ceiling_db)


def peak_limit(audio: np.ndarray, rate: int, ceiling_db: float = -1.0, window_ms: float = 5.0) -> np.ndarray:
    """Look-ahead peak limiter that only ever turns down (pedalboard's Limiter adds make-up gain, which would undo
    the loudness target). Gain = the smallest needed reduction within +-window, then averaged: never above need."""
    from scipy.ndimage import minimum_filter1d, uniform_filter1d
    ceiling = 10 ** (ceiling_db / 20)
    need = np.minimum(1.0, ceiling / np.maximum(np.abs(audio).max(axis=1), 1e-9))
    if need.min() >= 1.0:
        return audio
    w = max(1, int(rate * window_ms / 1000))
    gain = uniform_filter1d(minimum_filter1d(need, 2 * w + 1), w | 1)
    return audio * gain[:, None]


def clean_file(src: Path, s: VoiceSettings, noise: np.ndarray | None, dst: Path | None = None) -> Path:
    """Offline best-quality pass for a finished recording: denoise, the chain, loudness. Writes *_voice.wav."""
    import soundfile as sf
    audio, rate = sf.read(src, dtype="float32", always_2d=True)
    if s.enabled and s.denoise > 0:
        audio, _engine = denoise(audio, rate, s, noise)
    chain = build_chain(s, noise_floor_db(noise))
    if chain is not None:
        audio = chain(audio.T.astype(np.float32), rate).T
    if s.enabled and s.normalize:
        audio = normalize(audio, rate, s.target_lufs, s.limit_db)
    dst = dst or src.with_name(src.stem + "_voice.wav")
    sf.write(dst, np.clip(audio, -1, 1), rate, subtype="PCM_24")
    return dst
