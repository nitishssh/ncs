"""Shooting tools that run on any transport: interval/time-lapse, exposure bracketing, presets.

Sequences run in a daemon thread and report through callbacks, so the UI and scripts share them:

    seq = Interval(t, every=5, count=100)
    seq.start(on_progress=print, on_done=print)
    seq.stop()
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from camctl.labels import label

PRESETS_FILE = Path.home() / ".camctl" / "presets.json"


class Sequence:
    """Base: a cancellable job in a daemon thread. Subclasses implement steps()."""
    name = "sequence"

    def __init__(self, t):
        self.t = t
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, on_progress=lambda msg, i, n: None, on_done=lambda msg: None) -> None:
        def run():
            try:
                msg = self.steps(on_progress) or f"{self.name} finished"
            except Exception as e:
                msg = f"{self.name} stopped: {e}"
            on_done("stopped" if self._stop.is_set() else msg)
        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def sleep(self, seconds: float) -> bool:
        """Interruptible sleep; returns False if stopped."""
        return not self._stop.wait(max(seconds, 0))

    def steps(self, progress) -> str | None:
        raise NotImplementedError


class Interval(Sequence):
    """Take a photo (or record a short clip) every `every` seconds, `count` times (0 = until stopped)."""
    name = "Interval"

    def __init__(self, t, every: float, count: int, clip: float = 0, af: bool = True):
        super().__init__(t)
        self.every, self.count, self.clip, self.af = every, count, clip, af

    def steps(self, progress):
        i = 0
        while not self._stop.is_set() and (self.count == 0 or i < self.count):
            t0 = time.time()
            if self.clip > 0:
                self.t.record(True)
                if not self.sleep(self.clip):
                    self.t.record(False)
                    break
                self.t.record(False)
            else:
                self.t.shoot(autofocus=self.af)
            i += 1
            total = self.count or "∞"
            nxt = max(self.every - (time.time() - t0), 0)
            progress(f"{self.name}: {i}/{total} taken" + (f", next in {nxt:.0f}s" if self.count == 0 or i < self.count else ""),
                     i, self.count)
            if self.count and i >= self.count:
                break
            if not self.sleep(nxt):
                break
        return f"{self.name} finished: {i} {'clips' if self.clip else 'shots'}"


def exposure_steps(ability: list[str], current: str, frames: int, step_thirds: int) -> list[str]:
    """Bracket values centred on `current`, `step_thirds` thirds of a stop apart, clamped to the camera's range.
    Order: 0, -1, +1, -2, +2 … (the usual camera order)."""
    idx = ability.index(current)
    out = []
    for k in [0] + [s * n for n in range(1, frames // 2 + 1) for s in (-1, 1)]:
        j = min(max(idx + k * step_thirds, 0), len(ability) - 1)
        out.append(ability[j])
    return out


class Bracket(Sequence):
    """Exposure bracketing via exposure compensation (needs P, Tv or Av: in M or A+ it's fixed)."""
    name = "Bracket"

    def __init__(self, t, frames: int = 3, step_thirds: int = 3, af: bool = True, settle: float = 1.2):
        super().__init__(t)
        self.frames, self.step_thirds, self.af, self.settle = frames, step_thirds, af, settle

    def steps(self, progress):
        exp = self.t.get_setting("exposure")
        ability, original = exp.get("ability", []), exp["value"]
        if len(ability) < 2:
            raise RuntimeError("exposure compensation is locked - set the mode dial to P, Tv or Av")
        values = exposure_steps(ability, original, self.frames, self.step_thirds)
        try:
            for i, v in enumerate(values, 1):
                if self._stop.is_set():
                    break
                self.t.set_setting("exposure", v)
                self.sleep(0.3)  # let the camera apply it before the shutter
                self.t.shoot(autofocus=self.af and i == 1)  # focus once, keep it for the series
                progress(f"{self.name}: {i}/{len(values)} at {label('exposure', v)}", i, len(values))
                self.sleep(self.settle)
        finally:
            self.t.set_setting("exposure", original)
        shown = ", ".join(label("exposure", v) for v in values)
        return f"{self.name} finished: {len(values)} frames ({shown}); restored {label('exposure', original)}"


# ---- presets: named groups of settings, stored per user
def load_presets() -> dict[str, dict[str, str]]:
    try:
        return json.loads(PRESETS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_preset(name: str, values: dict[str, str]) -> None:
    presets = load_presets()
    presets[name] = values
    PRESETS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PRESETS_FILE.write_text(json.dumps(presets, indent=2))


def delete_preset(name: str) -> None:
    presets = load_presets()
    if presets.pop(name, None) is not None:
        PRESETS_FILE.write_text(json.dumps(presets, indent=2))


def apply_preset(t, values: dict[str, str]) -> tuple[list[str], list[str]]:
    """Apply in order; movie size first (it can change other abilities). Returns (applied, failed)."""
    order = sorted(values, key=lambda k: 0 if k == "movie_size" else 1)
    ok, failed = [], []
    for k in order:
        try:
            t.set_setting(k, values[k])
            ok.append(k)
        except Exception:
            failed.append(k)
    return ok, failed
