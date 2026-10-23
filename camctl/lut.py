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
