"""Voice-into-clip sync on synthetic shoots (needs ffmpeg: system or imageio-ffmpeg)."""
from __future__ import annotations

import subprocess

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

from studio_utility import sync

SR = 48000


def speech_like(seconds: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(SR * seconds)
    lp = signal.sosfilt(signal.butter(2, 4, fs=SR, output="sos"), rng.normal(0, 1, n))
    env = (lp > np.percentile(lp, 45)).astype(float)
    band = signal.sosfilt(signal.butter(4, [200, 3000], "band", fs=SR, output="sos"), rng.normal(0, 1, n))
    return (band * env * 0.3).astype(np.float32)


def shoot(folder, voice_offset: float, seed: int = 3, unrelated: bool = False, clip_s: float = 8):
    """Write a camera clip (created 12:00:02) and a mic recording; voice_offset = when the voice starts
    relative to the video (negative: mic started first). Camera audio = muffled, noisy copy of the voice."""
    speech = speech_like(30, seed)
    start = 10.0  # the 'true' timeline: video starts at t=10 s of speech
    v0 = start + voice_offset
    voice = speech[int(v0 * SR):]
    sf.write(folder / "2026-10-23_120000_MicA_DJIMic.wav", np.stack([voice] * 2, 1), SR, subtype="PCM_24")
    cam_src = speech_like(30, seed + 99) if unrelated else speech
    cam = cam_src[int(start * SR):int((start + clip_s) * SR)]
    cam = signal.sosfilt(signal.butter(2, 2500, fs=SR, output="sos"), cam) * 0.4
    cam = cam + np.random.default_rng(1).normal(0, 0.02, len(cam))
    sf.write(folder / "cam.wav", np.stack([cam] * 2, 1).astype(np.float32), SR)
    clip = folder / "A_0001C099_CANON.MP4"
    subprocess.run([sync.ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                    "-i", str(folder / "cam.wav"), "-t", str(clip_s), "-c:v", "libx264", "-c:a", "aac",
                    "-metadata", "creation_time=2026-10-23T12:00:02Z", "-shortest", str(clip)], check=True)
    (folder / "cam.wav").unlink()
    return clip


@pytest.mark.parametrize("voice_offset", [-2.37, 1.25, 0.0])
def test_finds_offset_and_merges(tmp_path, voice_offset):
    shoot(tmp_path, voice_offset)
    (r,) = sync.sync_folder(tmp_path)
    assert r.error is None and r.offset.ok
    assert r.offset.seconds == pytest.approx(voice_offset, abs=0.002)
    # in the final file, the voice track lines up with the camera audio
    final, cam = sync.read_audio(r.output), sync.read_audio(r.clip)
    assert abs(sync.find_offset(cam, final, max_shift_s=1).seconds) < 0.002


def test_final_has_untouched_video_and_two_audio_tracks(tmp_path):
    shoot(tmp_path, -1.0)
    (r,) = sync.sync_folder(tmp_path)
    md5 = lambda p: subprocess.run([sync.ffmpeg_exe(), "-v", "error", "-i", str(p), "-map", "0:v", "-c", "copy",
                                    "-f", "md5", "-"], capture_output=True, text=True, check=True).stdout
    assert md5(r.output) == md5(r.clip)  # picture copied bit-for-bit
    err = subprocess.run([sync.ffmpeg_exe(), "-hide_banner", "-i", str(r.output)], capture_output=True, text=True).stderr
    assert err.count("Audio:") == 2  # clean voice (default) + camera scratch
    assert sync.media_info(r.output)["duration"] == pytest.approx(sync.media_info(r.clip)["duration"], abs=0.1)


def test_refuses_unrelated_audio(tmp_path):
    shoot(tmp_path, -1.0, unrelated=True)
    (r,) = sync.sync_folder(tmp_path)
    assert r.output is None and "no clear match" in r.error


def test_pairing_prefers_cleaned_voice_and_skips_done(tmp_path):
    clip = shoot(tmp_path, 0.0)
    raw = tmp_path / "2026-10-23_120000_MicA_DJIMic.wav"
    cleaned = raw.with_name(raw.stem + "_voice.wav")
    cleaned.write_bytes(raw.read_bytes())
    assert sync.pair_clips(tmp_path) == [(clip, cleaned)]
    (tmp_path / "A_0001C099_CANON_final.mp4").write_bytes(b"done")
    assert sync.pair_clips(tmp_path) == []


def test_no_pair_when_times_are_far_apart(tmp_path):
    shoot(tmp_path, 0.0)
    (tmp_path / "2026-10-23_120000_MicA_DJIMic.wav").rename(tmp_path / "2026-10-23_140000_MicA_DJIMic.wav")
    assert sync.pair_clips(tmp_path) == []
