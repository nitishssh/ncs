"""Put the clean external-mic voice into each camera clip, automatically and losslessly for the picture.

1. pair_clips():   match each camera clip with the voice recording made at the same time (by timestamps)
2. find_offset():  align them precisely by cross-correlating the camera's own (scratch) audio with the voice
3. merge():        write <clip>_final.mp4: camera video copied untouched, clean voice as the main audio track,
                   the camera's scratch audio kept as a second track (safety)

Waveform sync works even if the camera and computer clocks differ by a few seconds, and needs no
timecode, clapper or slate (the approach of PluralEyes/KlikSync-style tools, here open and local)."""
from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

ANALYSIS_RATE = 8000   # speech envelope matching doesn't need more; keeps correlation fast
MAX_SEARCH_S = 30      # how far apart (seconds) clip and voice may start
MIN_CONFIDENCE = 6.0   # z-score of the correlation peak: matches measured 11-14, unrelated audio ~3.3
MIN_SIMILARITY = 0.2   # correlation coefficient of the aligned envelopes (matches ~0.99, unrelated ~0.2)
VIDEO_EXT = (".mp4", ".mov", ".mxf")


def ffmpeg_exe() -> str:
    """System ffmpeg if installed, else the one bundled with imageio-ffmpeg (packaged apps)."""
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def read_audio(path: Path, rate: int = ANALYSIS_RATE, seconds: float | None = None) -> np.ndarray:
    """Mono float32 audio of any file ffmpeg can read."""
    cmd = [ffmpeg_exe(), "-v", "error", "-i", str(path)]
    if seconds:
        cmd += ["-t", str(seconds)]
    cmd += ["-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, np.float32).copy()


def media_info(path: Path) -> dict:
    """duration (s), has_audio, creation (datetime, camera-local) parsed from `ffmpeg -i`."""
    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    dur = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    c = re.search(r"creation_time\s*:\s*(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)", err)
    # Canon writes the camera's local time but labels it UTC ("Z"); treat it as local
    created = datetime.fromisoformat(c[1]) if c else None
    return {"duration": dur, "has_audio": "Audio:" in err, "creation": created}


def _envelope(x: np.ndarray, rate: int) -> np.ndarray:
    """High-passed, rectified, lightly smoothed signal: robust to EQ/denoise differences between mics."""
    from scipy import signal
    sos = signal.butter(4, [150, 3400], btype="band", fs=rate, output="sos")
    y = np.abs(signal.sosfilt(sos, x))
    win = max(1, rate // 200)  # 5 ms
    y = np.convolve(y, np.ones(win) / win, mode="same")
    return (y - y.mean()) / (y.std() + 1e-12)


@dataclass
class Offset:
    seconds: float      # voice starts this many seconds AFTER the video starts (negative: before)
    confidence: float   # z-score of the correlation peak among all tried shifts
    similarity: float = 0.0  # correlation coefficient of the two envelopes at that shift

    @property
    def ok(self) -> bool:
        return self.confidence >= MIN_CONFIDENCE and self.similarity >= MIN_SIMILARITY


def find_offset(camera_audio: np.ndarray, voice_audio: np.ndarray, rate: int = ANALYSIS_RATE,
                max_shift_s: float = MAX_SEARCH_S) -> Offset:
    """Cross-correlate the camera's scratch audio with the external voice recording."""
    from scipy import signal
    a, b = _envelope(camera_audio, rate), _envelope(voice_audio, rate)
    corr = signal.correlate(a, b, mode="full", method="fft")
    lags = signal.correlation_lags(len(a), len(b), mode="full")
    keep = np.abs(lags) <= max_shift_s * rate
    corr, lags = corr[keep], lags[keep]
    i = int(np.argmax(corr))
    # Random audio always has *some* peak over thousands of shifts; a real match stands far above the rest
    confidence = float((corr[i] - corr.mean()) / (corr.std() + 1e-12))
    lag = int(lags[i])
    x, y = (a[lag:lag + len(b)], b) if lag >= 0 else (a, b[-lag:-lag + len(a)])
    n = min(len(x), len(y))
    similarity = float(np.corrcoef(x[:n], y[:n])[0, 1]) if n > rate // 2 else 0.0
    # lag > 0: the voice's content appears later in the camera audio -> voice started after the video
    return Offset(seconds=lag / rate, confidence=confidence, similarity=similarity)


def merge(video: Path, voice: Path, offset_s: float, out: Path | None = None, keep_camera_audio: bool = True) -> Path:
    """Video stream copied untouched; voice shifted by offset_s, padded/trimmed to the clip's length."""
    out = out or video.with_name(video.stem + "_final.mp4")
    dur = media_info(video)["duration"]
    if offset_s >= 0:   # voice started after the video: delay it with silence
        voice_in = ["-i", str(voice)]
        afilter = f"[1:a]adelay={int(round(offset_s * 1000))}:all=1,apad,atrim=0:{dur:.3f}[v]"
    else:               # voice started before the video: skip its beginning
        voice_in = ["-ss", f"{-offset_s:.4f}", "-i", str(voice)]
        afilter = f"[1:a]apad,atrim=0:{dur:.3f}[v]"
    cmd = [ffmpeg_exe(), "-v", "error", "-y", "-i", str(video), *voice_in, "-filter_complex", afilter,
           "-map", "0:v:0", "-map", "[v]"]
    if keep_camera_audio and media_info(video)["has_audio"]:
        cmd += ["-map", "0:a:0"]
    cmd += ["-c:v", "copy", "-c:a", "aac", "-b:a", "320k", "-ar", "48000",
            "-metadata:s:a:0", "title=Voice (external mic)", "-metadata:s:a:1", "title=Camera scratch",
            "-disposition:a:0", "default", "-movflags", "+faststart", str(out)]
    subprocess.run(cmd, check=True, capture_output=True)
    return out


# ---- pairing clips with voice recordings in an import folder
_STAMP = re.compile(r"(\d{4}-\d\d-\d\d)_(\d{6})_")


def voice_start(wav: Path) -> datetime | None:
    m = _STAMP.match(wav.name)
    return datetime.strptime(m[1] + m[2], "%Y-%m-%d%H%M%S") if m else None


def best_voice_file(raw: Path) -> Path:
    """Prefer the cleaned <name>_voice.wav next to a raw recording."""
    cleaned = raw.with_name(raw.stem + "_voice.wav")
    return cleaned if cleaned.exists() else raw


def pair_clips(folder: Path, window_s: float = 120) -> list[tuple[Path, Path]]:
    """(clip, voice wav) pairs whose start times are within window_s; one voice per clip (Mic A preferred).
    Clips that already have a _final.mp4 are skipped."""
    raws = [w for w in folder.glob("*.wav") if not w.stem.endswith("_voice") and voice_start(w)]
    pairs = []
    for clip in sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXT and not p.stem.endswith("_final")):
        if clip.with_name(clip.stem + "_final.mp4").exists():
            continue
        start = media_info(clip)["creation"]
        if start is None:
            continue
        near = [w for w in raws if abs((voice_start(w) - start).total_seconds()) <= window_s]
        if near:
            near.sort(key=lambda w: (abs((voice_start(w) - start).total_seconds()), "MicA" not in w.name))
            pairs.append((clip, best_voice_file(near[0])))
    return pairs


@dataclass
class SyncResult:
    clip: Path
    voice: Path
    offset: Offset | None = None
    output: Path | None = None
    error: str | None = None


def sync_clip(clip: Path, voice: Path) -> SyncResult:
    r = SyncResult(clip, voice)
    try:
        info = media_info(clip)
        if not info["has_audio"]:
            r.error = "clip has no camera audio to sync against"
            return r
        # The raw recording carries the same room sound as the camera mic; align with it, merge the clean one
        raw = voice.with_name(voice.stem.removesuffix("_voice") + ".wav")
        r.offset = find_offset(read_audio(clip), read_audio(raw if raw.exists() else voice))
        if not r.offset.ok:
            r.error = (f"no clear match (confidence {r.offset.confidence:.1f}, similarity "
                       f"{r.offset.similarity:.2f}); left unmerged")
            return r
        r.output = merge(clip, voice, r.offset.seconds)
    except subprocess.CalledProcessError as e:
        r.error = (e.stderr or b"").decode(errors="replace").strip().splitlines()[-1:] or str(e)
        r.error = r.error[0] if isinstance(r.error, list) else r.error
    except Exception as e:
        r.error = str(e)
    return r


def sync_folder(folder: Path) -> list[SyncResult]:
    return [sync_clip(c, v) for c, v in pair_clips(folder)]
