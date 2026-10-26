"""Post-recording editor, without the UI: one take in memory, its cleaned version, a trim range, and export.

The raw recording on disk is never changed. Re-cleaning runs the same voice chain as the automatic _voice.wav
(denoise engine -> chain -> loudness), so what you hear in the editor is exactly what gets exported."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from studio_utility import advice, voice

FADE_S = 0.01  # short fades at trim points so a cut never clicks


@dataclass
class Take:
    path: Path
    audio: np.ndarray          # (frames, channels) float32, the raw recording
    rate: int
    cleaned: np.ndarray | None = None
    start: int = 0             # trim range, in frames
    end: int = 0

    @classmethod
    def load(cls, path: Path) -> "Take":
        import soundfile as sf
        audio, rate = sf.read(path, dtype="float32", always_2d=True)
        take = cls(Path(path), audio, rate, end=len(audio))
        raw = take.raw_path()
        cleaned = raw.with_name(raw.stem + "_voice.wav")
        if Path(path) != cleaned and cleaned.exists():  # the auto-clean copy, if it was made
            c, r = sf.read(cleaned, dtype="float32", always_2d=True)
            if r == rate and len(c) == len(audio):
                take.cleaned = c
        return take

    def raw_path(self) -> Path:
        """Opening a _voice.wav edits its raw recording's family: exports are named after the raw take."""
        stem = self.path.stem
        return self.path.with_name(stem[:-6] + self.path.suffix) if stem.endswith("_voice") else self.path

    @property
    def duration(self) -> float:
        return len(self.audio) / self.rate

    def version(self, cleaned: bool) -> np.ndarray:
        return self.cleaned if cleaned and self.cleaned is not None else self.audio

    # ---- trim
    def set_trim(self, start_s: float, end_s: float):
        a, b = sorted((start_s, end_s))
        self.start = int(np.clip(a * self.rate, 0, len(self.audio)))
        self.end = int(np.clip(b * self.rate, 0, len(self.audio)))
        if self.end - self.start < int(0.05 * self.rate):  # a click, not a selection: keep everything
            self.start, self.end = 0, len(self.audio)

    def reset_trim(self):
        self.start, self.end = 0, len(self.audio)

    @property
    def trimmed(self) -> bool:
        return (self.start, self.end) != (0, len(self.audio))

    def selection(self, cleaned: bool) -> np.ndarray:
        x = self.version(cleaned)[self.start:self.end].copy()
        n = min(int(FADE_S * self.rate), len(x) // 2)
        if n and self.trimmed:
            ramp = np.linspace(0, 1, n, dtype=np.float32)[:, None]
            if self.start > 0:
                x[:n] *= ramp
            if self.end < len(self.audio):
                x[-n:] *= ramp[::-1]
        return x

    # ---- waveform
    def peaks(self, width: int, cleaned: bool = False, start: int = 0, end: int | None = None) -> np.ndarray:
        """(width, 2) min/max of the mono mix per pixel column, for drawing. start/end zoom into a range."""
        x = self.version(cleaned)[start:end if end is not None else len(self.audio)].mean(axis=1)
        width = max(1, width)
        if not len(x):
            return np.zeros((width, 2), np.float32)
        edges = np.linspace(0, len(x), width + 1).astype(int)
        edges[1:] = np.maximum(edges[1:], edges[:-1] + 1)
        edges = np.minimum(edges, len(x))
        lo = np.minimum.reduceat(x, edges[:-1])
        hi = np.maximum.reduceat(x, edges[:-1])
        return np.stack([lo, hi], axis=1).astype(np.float32)

    # ---- cleaning
    def clean(self, settings: voice.VoiceSettings, noise: np.ndarray | None) -> str:
        """Re-run the voice processing on the whole take (so trim changes never need a re-clean). Returns the
        denoise engine used."""
        audio, used = self.audio, "off"
        if settings.enabled and settings.denoise > 0:
            audio, used = voice.denoise(audio, self.rate, settings, noise)
        chain = voice.build_chain(settings, voice.noise_floor_db(noise))
        if chain is not None:
            audio = chain(np.ascontiguousarray(audio.T, np.float32), self.rate).T
        if settings.enabled and settings.normalize:
            audio = voice.normalize(audio, self.rate, settings.target_lufs, settings.limit_db)
        self.cleaned = np.clip(audio, -1, 1).astype(np.float32)
        return used

    def report(self, cleaned: bool) -> advice.Report:
        rep = advice.analyze(self.selection(cleaned), self.rate)
        if cleaned:  # peaks near the ceiling are the loudness limiter doing its job, not a gain problem
            rep.tips = [t for t in rep.tips if not t.title.startswith("Very hot")] or [advice.Tip("ok", "Sounds good", "")]
        return rep

    # ---- export
    def export_path(self, cleaned: bool) -> Path:
        raw = self.raw_path()
        stem = f"{raw.stem}{'_voice' if cleaned else ''}"
        if not self.trimmed:  # the full take: same content every time, so the name stays fixed
            return raw.with_name(stem + ".wav")
        path, n = raw.with_name(stem + "_edit.wav"), 2
        while path.exists():  # each cut is the user's own work: a new trim never replaces an earlier one
            path, n = raw.with_name(f"{stem}-{n}_edit.wav"), n + 1  # keeps the _edit ending sync skips
        return path

    def export(self, cleaned: bool, path: Path | None = None) -> Path:
        """Write the selection (or everything) as 24-bit WAV; never overwrites the raw recording."""
        import soundfile as sf
        path = Path(path or self.export_path(cleaned))
        if path.resolve() == self.raw_path().resolve():
            raise ValueError("refusing to overwrite the original recording")
        sf.write(path, self.selection(cleaned), self.rate, subtype="PCM_24")
        return path
