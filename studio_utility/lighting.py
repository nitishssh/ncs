"""Lighting simulator: key/fill/back lights on a subject -> suggested exposure and white balance.

Incident-meter model: each light gives lux_at_1m / distance² at the subject, weighted by how much it
faces the camera side (a back light rims the subject but barely changes the face exposure). EV100 from
incident lux is log2(lux / 2.5) (calibration constant C = 250, ISO 100).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import numpy as np

C_INCIDENT = 2.5  # lux at EV0, ISO 100
# Kelvin the camera's named WB presets assume (Canon defaults)
WB_PRESETS = {"tungsten": 3200, "fluorescent": 4000, "daylight": 5200, "cloudy": 6000, "shade": 7000}


@dataclass
class Light:
    name: str
    lux_1m: float  # output at 1 m
    distance: float = 1.0  # m
    azimuth: float = 45.0  # degrees around the subject, 0 = from the camera, 180 = from behind
    elevation: float = 30.0  # degrees above the subject's eye line
    kelvin: int = 5600
    on: bool = True

    def lux(self) -> float:
        return self.lux_1m / max(self.distance, 0.1) ** 2 if self.on else 0.0

    def direction(self) -> np.ndarray:
        """Unit vector from the subject toward the light (x right, y up, z toward camera)."""
        az, el = math.radians(self.azimuth), math.radians(self.elevation)
        return np.array([math.sin(az) * math.cos(el), math.sin(el), math.cos(az) * math.cos(el)])


@dataclass
class Scene:
    lights: list[Light] = field(default_factory=lambda: [
        Light("Key", 2000, 1.2, 45, 30, 5600),
        Light("Fill", 800, 2.0, -45, 10, 5600),
        Light("Back", 1000, 1.5, 160, 45, 5600),
    ])
    ambient_lux: float = 30.0
    ambient_kelvin: int = 4000

    def subject_lux(self) -> float:
        """What an incident meter at the face, pointed at the camera, reads."""
        front = sum(l.lux() * max(l.direction()[2], 0.0) for l in self.lights)
        return front + self.ambient_lux

    def ev100(self) -> float:
        return math.log2(max(self.subject_lux(), 0.01) / C_INCIDENT)

    def kelvin(self) -> int:
        """Lux-weighted mix in mireds (how colour temperatures actually blend)."""
        parts = [(l.lux() * max(l.direction()[2], 0.0), l.kelvin) for l in self.lights]
        parts.append((self.ambient_lux, self.ambient_kelvin))
        total = sum(w for w, _ in parts)
        if total <= 0:
            return 5600
        mired = sum(w * 1e6 / k for w, k in parts) / total
        return int(round(1e6 / mired / 100) * 100)

    def ratio(self) -> float | None:
        """Key:fill ratio in stops (None without both lights on)."""
        key, fill = (next((l for l in self.lights if l.name == n), None) for n in ("Key", "Fill"))
        if not key or not fill or not key.lux() or not fill.lux():
            return None
        return math.log2((key.lux() + fill.lux()) / fill.lux())


# --- parsing the camera's value strings ---

def parse_tv(s: str) -> float | None:
    s = s.strip().replace("″", '"')
    if m := re.fullmatch(r"1/(\d+(?:\.\d+)?)", s):
        return 1 / float(m[1])
    if m := re.fullmatch(r'(\d+)"(\d*)', s):  # Canon writes 0.5 s as 0"5
        return float(f"{m[1]}.{m[2] or 0}")
    try:
        return float(s.rstrip("s"))
    except ValueError:
        return None


def parse_av(s: str) -> float | None:
    try:
        return float(s.strip().lower().lstrip("f").lstrip("/"))
    except ValueError:
        return None


def parse_iso(s: str) -> float | None:
    try:
        return float(s)
    except ValueError:
        return None


def _nearest(choices: list[str], parse, target: float, log: bool = True) -> str | None:
    vals = [(c, parse(c)) for c in choices]
    vals = [(c, v) for c, v in vals if v and v > 0]
    if not vals:
        return None
    f = (lambda v: math.log2(v)) if log else (lambda v: v)
    return min(vals, key=lambda cv: abs(f(cv[1]) - f(target)))[0]


@dataclass
class Suggestion:
    tv: str | None
    av: str | None
    iso: str | None
    wb: str | None
    color_temp: str | None
    error_stops: float  # + = the snapped settings overexpose
    notes: list[str]

    def settings(self) -> dict[str, str]:
        return {k: v for k, v in (("tv", self.tv), ("av", self.av), ("iso", self.iso), ("wb", self.wb),
                                  ("color_temp", self.color_temp)) if v}


DEFAULT_CHOICES = {
    "tv": ["1/8000", "1/4000", "1/2000", "1/1000", "1/500", "1/250", "1/125", "1/100", "1/60", "1/50", "1/30",
           "1/15", "1/8", "1/4", '0"5', '1"'],
    "av": ["f1.8", "f2.8", "f4.0", "f5.6", "f8.0", "f11", "f16"],
    "iso": ["100", "200", "400", "800", "1600", "3200", "6400", "12800"],
    "wb": ["auto", *WB_PRESETS],
}


def suggest(scene: Scene, aperture: float, fps: float | None = None, choices: dict[str, list[str]] | None = None
            ) -> Suggestion:
    """Exposure for the face at `aperture`. Movie (fps given) locks the shutter to 180°; photo starts at 1/125."""
    ch = {**DEFAULT_CHOICES, **{k: v for k, v in (choices or {}).items() if v}}
    notes = []
    ev = scene.ev100()
    av = _nearest(ch["av"], parse_av, aperture) or f"f{aperture}"
    n = parse_av(av)
    t = 1 / (2 * fps) if fps else 1 / 125
    tv = _nearest(ch["tv"], parse_tv, t)
    t = parse_tv(tv) if tv else t
    iso = 100 * n * n / t / 2 ** ev
    isos = [v for v in map(parse_iso, ch["iso"]) if v]
    lo, hi = (min(isos), max(isos)) if isos else (100, 12800)
    if iso < lo and not fps:  # photo: trade shutter for the extra light before giving up
        t = t * iso / lo
        tv = _nearest(ch["tv"], parse_tv, t)
        t = parse_tv(tv) or t
        iso = 100 * n * n / t / 2 ** ev
    if iso < lo:
        notes.append(f"Too bright by {math.log2(lo / iso):.1f} stops at ISO {lo:g}: stop down, dim the lights or add ND.")
    elif iso > hi:
        notes.append(f"Too dark by {math.log2(iso / hi):.1f} stops at ISO {hi:g}: open up, add light or move it closer.")
    elif iso > 3200:
        notes.append("High ISO: expect noise. Bring the key closer to buy back stops.")
    iso_s = _nearest(ch["iso"], parse_iso, iso)
    real_iso = parse_iso(iso_s) if iso_s else iso
    error = math.log2(real_iso * t * 2 ** ev / (100 * n * n))

    k = scene.kelvin()
    enough = scene.subject_lux() * 0.1  # a source under a tenth of the light can't visibly shift the face
    mixed = {l.kelvin for l in scene.lights if l.lux() >= enough}
    if scene.ambient_lux >= enough:
        mixed.add(scene.ambient_kelvin)
    if mixed and max(mixed) - min(mixed) >= 1500:
        notes.append("Mixed colour temperatures: gel the odd light out or the face will shift colour.")
    # A Kelvin value only takes effect in the camera's colour temperature WB mode
    kelvin_wb = next((c for c in ch["wb"] if c.lower() in ("colortemp", "color temperature", "color temp")), None)
    ct = _nearest(ch.get("color_temp", []), parse_iso, k, log=False) if kelvin_wb else None
    named = {c: WB_PRESETS[c] for c in ch["wb"] if c in WB_PRESETS}
    if ct:
        wb = kelvin_wb
    elif named:
        wb = min(named, key=lambda c: abs(1e6 / named[c] - 1e6 / k))
    else:
        wb = None
    ratio = scene.ratio()
    if ratio is not None:
        notes.append(f"Key:fill {2 ** ratio:.1f}:1 ({ratio:.1f} stops)"
                     + (" - flat" if ratio < 1 else " - dramatic" if ratio > 3 else ""))
    return Suggestion(tv, av, iso_s, wb, ct, round(error, 2), notes)


# --- preview ---

def kelvin_rgb(k: float) -> np.ndarray:
    """Approximate blackbody colour (Tanner Helland's fit), 0..1."""
    t = k / 100
    r = 255 if t <= 66 else 329.698727446 * (t - 60) ** -0.1332047592
    g = 99.4708025861 * math.log(t) - 161.1195681661 if t <= 66 else 288.1221695283 * (t - 60) ** -0.0755148492
    b = 255 if t >= 66 else 0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    return np.clip(np.array([r, g, b]) / 255, 0, 1)


def render(scene: Scene, wb_kelvin: float, exposure_error: float = 0.0, size: int = 220) -> np.ndarray:
    """RGB uint8 image of a lit sphere (the 'head') on a dark backdrop, white-balanced to wb_kelvin.
    exposure_error in stops brightens/darkens it the way the snapped settings would."""
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    cx, cy, r = size / 2, size * 0.48, size * 0.34
    nx, ny = (xs - cx) / r, -(ys - cy) / r
    inside = nx ** 2 + ny ** 2 <= 1
    nz = np.sqrt(np.clip(1 - nx ** 2 - ny ** 2, 0, 1))
    normals = np.stack([nx, ny, nz], -1)
    balance = 1 / np.maximum(kelvin_rgb(wb_kelvin), 1e-3)
    balance /= balance.max()
    ref = max(scene.subject_lux(), 1e-3)  # correct exposure puts the metered face at mid grey
    skin = np.array([0.85, 0.65, 0.55])
    img = np.zeros((size, size, 3), np.float32)
    for l in scene.lights:
        if not l.lux():
            continue
        lam = np.clip(normals @ l.direction().astype(np.float32), 0, None)
        if l.name == "Back":  # rim: only the edge facing away from the camera catches it
            lam = lam * (1 - nz) ** 2 * 2.5
        img += lam[..., None] * (l.lux() / ref) * kelvin_rgb(l.kelvin) * balance
    img += (scene.ambient_lux / ref) * kelvin_rgb(scene.ambient_kelvin) * balance
    img *= skin
    backdrop = np.full((size, size, 3), 0.05, np.float32) * (scene.ambient_lux / ref * 4 + 0.2)
    img = np.where(inside[..., None], img, backdrop)
    img *= 0.45 * 2 ** exposure_error
    img = img / (1 + img * 0.15)  # gentle highlight roll-off
    return (np.clip(img, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)
