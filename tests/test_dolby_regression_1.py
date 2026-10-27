"""Dolby export regressions found by /qa: honest errors and exit codes for scripts."""
from __future__ import annotations

import subprocess
import sys

import pytest

from studio_utility import dolby
from studio_utility.sync import ffmpeg_exe


def silent_clip(folder):
    out = folder / "silent.mp4"
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=25",
                    "-t", "1", "-c:v", "libx264", str(out)], check=True)
    return out


# Regression: ISSUE-004 — a clip path that doesn't exist was reported as "has no audio track"
# Found by /qa on 2026-10-27
# Report: .gstack/qa-reports/qa-report-studio-utility-hub-2026-10-27.md
@pytest.mark.parametrize("export", [lambda p: dolby.audio(p, "ac3"), lambda p: dolby.vision(p, allow_sdr=True)])
def test_missing_clip_says_not_found(tmp_path, export, monkeypatch):
    monkeypatch.setattr(dolby, "missing_tools", lambda: [])
    with pytest.raises(RuntimeError, match="not found"):
        export(tmp_path / "nope.mp4")


# Regression: ISSUE-003 — `studio-utility dolby` exited 0 with the error on stdout when an export failed
# Found by /qa on 2026-10-27
# Report: .gstack/qa-reports/qa-report-studio-utility-hub-2026-10-27.md
def test_cli_failure_exits_non_zero_on_stderr(tmp_path):
    r = subprocess.run([sys.executable, "-m", "studio_utility", "dolby", "eac3", str(silent_clip(tmp_path))],
                       capture_output=True, text=True)
    assert r.returncode == 1
    assert "no audio track" in r.stderr and r.stdout == ""
    assert not (tmp_path / "silent_eac3.mp4").exists()
