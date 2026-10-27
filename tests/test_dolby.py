"""Dolby exports on synthetic clips (Dolby Vision needs x265, dovi_tool and MP4Box on PATH)."""
from __future__ import annotations

import subprocess

import pytest

from studio_utility import dolby
from studio_utility.sync import ffmpeg_exe


def clip(folder, pq: bool = True):
    out = folder / ("pq.mp4" if pq else "sdr.mp4")
    tags = ":colorprim=bt2020:transfer=smpte2084:colormatrix=bt2020nc" if pq else ""
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                    "-f", "lavfi", "-i", "sine=f=440", "-t", "1", "-c:v", "libx265", "-pix_fmt", "yuv420p10le",
                    "-x265-params", "log-level=error" + tags, "-c:a", "aac", str(out)], check=True)
    return out


def info(path) -> str:
    return subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True).stderr


def test_probe_reads_frames_and_pq(tmp_path):
    assert dolby.probe(clip(tmp_path)) == {"frames": 25, "fps": "25", "is_pq": True}
    assert not dolby.probe(clip(tmp_path, pq=False))["is_pq"]


@pytest.mark.parametrize("codec", ["ac3", "eac3"])
def test_audio_copies_video_and_encodes_dolby(tmp_path, codec):
    out = dolby.audio(clip(tmp_path), codec)
    assert out.name == f"pq_{codec}.mp4"
    assert f"Audio: {codec}" in info(out) and "Video: hevc" in info(out)


def test_rpu_config_covers_every_frame():
    cfg = dolby.rpu_config(120)
    assert cfg["length"] == 120 and cfg["profile"] == "8.1"


def test_vision_refuses_sdr(tmp_path):
    if dolby.missing_tools():
        pytest.skip("Dolby Vision tools not installed")
    with pytest.raises(RuntimeError, match="not HDR PQ"):
        dolby.vision(clip(tmp_path, pq=False))


def test_vision_writes_profile_8_1(tmp_path):
    if dolby.missing_tools():
        pytest.skip("Dolby Vision tools not installed")
    text = info(dolby.vision(clip(tmp_path), preset="ultrafast"))
    assert "DOVI configuration record" in text and "profile: 8" in text and "smpte2084" in text
