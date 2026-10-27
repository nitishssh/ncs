"""Dolby exports of downloaded clips, built from free tools (nothing from Dolby is bundled).

- vision(): <clip>_dv.mp4, Dolby Vision profile 8.1 (HDR10-compatible base layer + RPU metadata).
            Needs an HDR PQ clip (camera set to HDR PQ, not Canon Log 3) and x265, dovi_tool, MP4Box.
- audio():  <clip>_ac3.mp4 / <clip>_eac3.mp4, picture copied untouched, audio as Dolby Digital (Plus).
            ffmpeg alone is enough (system or imageio-ffmpeg).

Dolby Vision steps: count frames -> dovi_tool generate (static L6 metadata, one RPU per frame) ->
ffmpeg decodes to 10-bit y4m piped into x265 with the RPU -> MP4Box muxes with dvp=8.1 and the clip's audio."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from studio_utility.sync import ffmpeg_exe

VISION_TOOLS = ("x265", "dovi_tool", "MP4Box")
AUDIO_CODECS = {"ac3": ("Dolby Digital", "448k"), "eac3": ("Dolby Digital Plus", "640k")}
# Mastering display assumed for camera HDR PQ: P3-D65 primaries, 1000 nit peak (what the R50 V's PQ targets)
MASTER_DISPLAY = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
MAX_CLL, MAX_FALL = 1000, 400


def missing_tools() -> list[str]:
    """Dolby Vision tools not on PATH (brew install x265 dovi_tool gpac)."""
    return [t for t in VISION_TOOLS if not shutil.which(t)]


def probe(path: Path) -> dict:
    """frames, fps (str), is_pq (BT.2020 PQ video) parsed from ffmpeg."""
    err = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path), "-map", "0:v:0", "-c", "copy",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    video = next((ln for ln in err.splitlines() if "Video:" in ln), "")
    frames = re.findall(r"frame=\s*(\d+)", err)
    fps = re.search(r"([\d.]+) fps", video)
    return {"frames": int(frames[-1]) if frames else 0, "fps": fps[1] if fps else "",
            "is_pq": "smpte2084" in video}


def _run(cmd: list[str], **kw):
    r = subprocess.run(cmd, capture_output=True, **kw)
    if r.returncode:
        tail = (r.stderr or b"").decode(errors="replace").strip().splitlines()[-3:]
        raise RuntimeError(f"{Path(cmd[0]).name} failed: {' | '.join(tail) or r.returncode}")
    return r


def audio(clip: Path, codec: str = "eac3", out: Path | None = None) -> Path:
    """Re-encode every audio track to AC-3 / E-AC-3; video and track titles copied as they are."""
    name, rate = AUDIO_CODECS[codec]
    streams = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(clip)], capture_output=True, text=True).stderr
    if "Audio:" not in streams:  # otherwise ffmpeg happily writes a video-only copy with no Dolby audio
        raise RuntimeError(f"{clip.name} has no audio track to encode as {name}")
    out = out or clip.with_name(f"{clip.stem}_{codec}.mp4")
    _run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(clip), "-map", "0:v:0", "-map", "0:a",
          "-c:v", "copy", "-c:a", codec, "-b:a", rate, "-ar", "48000", "-map_metadata", "0",
          "-movflags", "+faststart", str(out)])
    return out


def rpu_config(frames: int) -> dict:
    """dovi_tool generator config: static profile 8.1 metadata for every frame."""
    return {"cm_version": "V40", "profile": "8.1", "length": frames,
            "level6": {"max_display_mastering_luminance": 1000, "min_display_mastering_luminance": 1,
                       "max_content_light_level": MAX_CLL, "max_frame_average_light_level": MAX_FALL}}


def vision(clip: Path, out: Path | None = None, crf: int = 18, preset: str = "slow",
           allow_sdr: bool = False) -> Path:
    """Dolby Vision 8.1 MP4. Re-encodes the picture (slow: roughly real time or slower on a laptop)."""
    if missing := missing_tools():
        raise RuntimeError(f"missing {', '.join(missing)} (brew install x265 dovi_tool gpac)")
    info = probe(clip)
    if not info["frames"]:
        raise RuntimeError(f"{clip.name}: no video frames found")
    if not info["is_pq"] and not allow_sdr:
        raise RuntimeError(f"{clip.name} is not HDR PQ; shoot with HDR PQ on (Canon Log 3 and SDR need grading first)")
    out = out or clip.with_name(clip.stem + "_dv.mp4")
    with tempfile.TemporaryDirectory(prefix="dolby-") as tmp:
        tmp = Path(tmp)
        cfg, rpu, hevc, m4a = tmp / "rpu.json", tmp / "rpu.bin", tmp / "video.hevc", tmp / "audio.m4a"
        cfg.write_text(json.dumps(rpu_config(info["frames"])))
        _run(["dovi_tool", "generate", "-j", str(cfg), "-o", str(rpu)])

        dec = subprocess.Popen([ffmpeg_exe(), "-v", "error", "-i", str(clip), "-map", "0:v:0",
                                "-pix_fmt", "yuv420p10le", "-strict", "-1", "-f", "yuv4mpegpipe", "-"],
                               stdout=subprocess.PIPE)
        try:
            _run(["x265", "--y4m", "--input", "-", "--output", str(hevc), "--preset", preset, "--crf", str(crf),
                  "--profile", "main10", "--output-depth", "10", "--colorprim", "bt2020", "--transfer", "smpte2084",
                  "--colormatrix", "bt2020nc", "--range", "limited", "--hdr10", "--hdr10-opt", "--repeat-headers",
                  "--master-display", MASTER_DISPLAY, "--max-cll", f"{MAX_CLL},{MAX_FALL}",
                  "--vbv-bufsize", "160000", "--vbv-maxrate", "160000",
                  "--dolby-vision-profile", "8.1", "--dolby-vision-rpu", str(rpu)], stdin=dec.stdout)
        finally:
            dec.stdout.close()
            dec.wait()

        mux = ["MP4Box", "-quiet", "-add", f"{hevc}:dvp=8.1:fps={info['fps'] or 25}"]
        if subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(clip), "-map", "0:a:0", "-vn",
                           "-c:a", "aac", "-b:a", "320k", str(m4a)], capture_output=True).returncode == 0:
            mux += ["-add", str(m4a)]
        out.unlink(missing_ok=True)
        _run(mux + ["-new", str(out)])
    return out
