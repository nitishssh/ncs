"""Human-readable labels for raw CCAPI setting values. The UI shows label(), sends the raw value."""
from __future__ import annotations

import re

FRACTIONS = {"1/3": "⅓", "2/3": "⅔", "1/2": "½"}
NAMED = {
    "auto": "Auto", "awbwhite": "AWB white", "whitefluorescent": "Fluorescent", "colortemp": "Color temp",
    "spot": "Spot AF", "1point": "1-point", "expand_cross": "Expand (cross)", "expand_around": "Expand (around)",
    "whole_area": "Whole area", "single": "Single",
}


def _fps(code: str) -> str:
    """CCAPI encodes frame rates ×100: 2500 → 25, 2997 → 29.97, 11988 → 119.88."""
    v = int(code) / 100
    return f"{v:g}p"


def _movie_quality(v: str) -> str | None:
    # e.g. 3840x2160_5000_longgop_standard_crop
    m = re.match(r"(\d+)x(\d+)_(\d+)_([a-z]+)(?:_([a-z]+))?(?:_(crop))?", v)
    if not m:
        return None
    w, h, fps, comp, _, crop = m.groups()
    res = {"3840": "4K", "1920": "FHD", "1280": "HD"}.get(w, f"{w}×{h}")
    label = f"{res}{' Crop' if crop else ''} {_fps(fps)}"
    return label + ("" if comp == "longgop" else f" {comp.upper()}")


def _exposure(v: str) -> str | None:
    # +0.0, -1.0, -2_2/3, +0_1/3
    m = re.fullmatch(r"([+-])(\d+)(?:\.0|_(\d/\d))", v)
    if not m:
        return None
    sign, whole, frac = m.groups()
    if whole == "0" and not frac:
        return "±0"
    sign = "−" if sign == "-" else "+"
    return f"{sign}{'' if whole == '0' and frac else whole}{FRACTIONS.get(frac, '') if frac else ''}"


def label(key: str, value) -> str:
    v = str(value)
    if key == "movie_size":
        return _movie_quality(v) or v
    if key == "exposure":
        return _exposure(v) or v
    if key == "av" and v.startswith("f") and not v.startswith("f/"):
        return "f/" + v[1:]
    if key == "tv" and v.endswith('"'):
        return v[:-1] + " s"
    if key == "tv" and re.fullmatch(r'\d+"\d+', v):  # Canon writes 0.3 s as 0"3, 1.3 s as 1"3
        return v.replace('"', ".") + " s"
    if v in NAMED:
        return NAMED[v]
    m = re.fullmatch(r"flexible_zone_(\d)", v)
    if m:
        return f"Flexible zone {m.group(1)}"
    if re.fullmatch(r"[a-z][a-z0-9_]*", v):
        return v.replace("_", " ").capitalize()
    return v
