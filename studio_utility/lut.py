"""Load .cube 3D LUTs and apply them to BGR frames (preview only; the camera still records log)."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def load_cube(path: str | Path) -> np.ndarray:
    """Return an (N, N, N, 3) float32 RGB table indexed [b][g][r], as .cube files are ordered."""
    size, rows = 0, []
    for line in Path(path).read_text().splitlines():
        parts = line.split()
        if not parts or parts[0].startswith("#"):
            continue
        if parts[0] == "LUT_3D_SIZE":
            size = int(parts[1])
        elif parts[0][0] in "-.0123456789":
            rows.append([float(v) for v in parts[:3]])
    if not size or len(rows) != size ** 3:
        raise ValueError(f"{Path(path).name} is not a valid 3D .cube LUT")
    return np.asarray(rows, np.float32).reshape(size, size, size, 3)


def apply_lut(frame: np.ndarray, lut: np.ndarray) -> np.ndarray:
    """Trilinear lookup on a BGR uint8 frame."""
    n = lut.shape[0]
    x = frame.astype(np.float32) * ((n - 1) / 255.0)  # B, G, R scaled to grid
    i0 = np.floor(x).astype(np.int32).clip(0, n - 2)
    f = x - i0
    b0, g0, r0 = i0[..., 0], i0[..., 1], i0[..., 2]
    fb, fg, fr = f[..., 0:1], f[..., 1:2], f[..., 2:3]
    out = 0
    for db, wb in ((0, 1 - fb), (1, fb)):
        for dg, wg in ((0, 1 - fg), (1, fg)):
            for dr, wr in ((0, 1 - fr), (1, fr)):
                out = out + lut[b0 + db, g0 + dg, r0 + dr] * (wb * wg * wr)
    rgb = (np.clip(out, 0, 1) * 255).astype(np.uint8)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


# ---- built-in preview LUT: Canon Log 3 -> Rec.709
def clog3_to_linear(x: np.ndarray) -> np.ndarray:
    """Canon Log 3 code value (0..1) -> scene linear, 1.0 = 90% white (Canon's published curve)."""
    x = np.asarray(x, np.float64)
    return np.where(x < 0.097465473, -(10 ** ((0.12783901 - x) / 0.36726845) - 1) / 14.98325,
                    np.where(x <= 0.15277891, (x - 0.12512219) / 1.9754798,
                             (10 ** ((x - 0.12240537) / 0.36726845) - 1) / 14.98325))


def linear_to_clog3(L: np.ndarray) -> np.ndarray:
    L = np.asarray(L, np.float64)
    with np.errstate(invalid="ignore"):  # np.where evaluates both branches; the unused one may be NaN
        return np.where(L < -0.014, -0.36726845 * np.log10(-L * 14.98325 + 1) + 0.12783901,
                        np.where(L <= 0.014, 1.9754798 * L + 0.12512219,
                                 0.36726845 * np.log10(L * 14.98325 + 1) + 0.12240537))


def _rec709_oetf(L: np.ndarray) -> np.ndarray:
    L = np.clip(L, 0, 1)
    return np.where(L < 0.018, 4.5 * L, 1.099 * L ** 0.45 - 0.099)


def clog3_to_rec709(size: int = 33, exposure: float = 0.0, saturation: float = 1.25) -> np.ndarray:
    """Preview table in load_cube()'s layout ([b][g][r] -> RGB).

    Approximation for monitoring: Canon's curve decoded exactly, a soft highlight shoulder instead of a hard
    clip, Rec.709 encoding, and a saturation lift standing in for the Cinema Gamut -> Rec.709 matrix.
    For final grading use Canon's official LUT (load it with Load LUT…)."""
    g = np.linspace(0, 1, size)
    b, gg, r = np.meshgrid(g, g, g, indexing="ij")
    rgb = np.stack([r, gg, b], -1)
    lin = clog3_to_linear(rgb) * 0.9 * (2 ** exposure)          # 1.0 = 100% reflectance
    lin = np.maximum(lin, 0)
    knee = 0.75                                                  # highlight roll-off above 75%
    over = np.maximum(lin - knee, 0)
    lin = np.where(lin > knee, knee + (1 - knee) * (1 - np.exp(-over / (1 - knee))), lin)
    luma = (lin * [0.2126, 0.7152, 0.0722]).sum(-1, keepdims=True)
    lin = np.maximum(luma + (lin - luma) * saturation, 0)
    return _rec709_oetf(lin).astype(np.float32)


def write_cube(table: np.ndarray, path: str | Path, title: str) -> Path:
    n = table.shape[0]
    lines = [f'TITLE "{title}"', f"LUT_3D_SIZE {n}", "DOMAIN_MIN 0 0 0", "DOMAIN_MAX 1 1 1"]
    lines += [f"{r:.6f} {g:.6f} {b:.6f}" for r, g, b in table.reshape(-1, 3)]
    path = Path(path)
    path.write_text("\n".join(lines) + "\n")
    return path


BUILTIN = {"Canon Log 3 → Rec.709 (preview)": clog3_to_rec709}
