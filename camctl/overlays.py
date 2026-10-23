"""Live view monitoring overlays drawn on BGR frames: histogram, AF frames, focus peaking, zebras."""
from __future__ import annotations

import cv2
import numpy as np

# Channel order of CCAPI liveviewdata.histogram: luminance, red, green, blue
HIST_COLORS = [(235, 235, 235), (80, 80, 255), (80, 220, 80), (255, 140, 60)]


def histogram_from_frame(frame: np.ndarray) -> list[list[int]]:
    """Same shape as the camera's histogram, for transports that don't send one."""
    y = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    chans = [y, frame[..., 2], frame[..., 1], frame[..., 0]]
    return [np.bincount(c.ravel(), minlength=256).tolist() for c in chans]


def draw_histogram(frame: np.ndarray, hist: list[list[int]], rgb: bool = False) -> np.ndarray:
    """Translucent histogram panel in the bottom-right corner."""
    h, w = frame.shape[:2]
    pw, ph = max(128, w // 4), max(64, h // 6)
    x0, y0 = w - pw - 10, h - ph - 10
    panel = frame[y0:y0 + ph, x0:x0 + pw]
    panel[:] = (panel * 0.35).astype(np.uint8)
    for i in ([1, 2, 3] if rgb else [0]):
        bins = np.asarray(hist[i], np.float64)
        bins = np.interp(np.linspace(0, 255, pw), np.arange(256), bins)
        bins = np.sqrt(bins / max(bins.max(), 1))  # sqrt keeps shadows/highlights visible
        pts = np.stack([np.arange(pw), ph - 1 - bins * (ph - 4)], 1).astype(np.int32)
        if rgb:
            cv2.polylines(panel, [pts], False, HIST_COLORS[i], 1, cv2.LINE_AA)
        else:
            poly = np.vstack([[[0, ph - 1]], pts, [[pw - 1, ph - 1]]])
            cv2.fillPoly(panel, [poly], HIST_COLORS[0])
    cv2.rectangle(frame, (x0, y0), (x0 + pw, y0 + ph), (90, 90, 90), 1)
    return frame


def draw_af_frames(frame: np.ndarray, afframes: list[dict], image: dict) -> np.ndarray:
    """AF frames come in sensor pixels; `image` is the live view's area on the sensor."""
    if not afframes or not image or not image.get("positionwidth"):
        return frame
    h, w = frame.shape[:2]
    sx, sy = w / image["positionwidth"], h / image["positionheight"]
    for a in afframes:
        x = int((a["x"] - image["positionx"]) * sx)
        y = int((a["y"] - image["positiony"]) * sy)
        x2, y2 = x + int(a["width"] * sx), y + int(a["height"] * sy)
        focused = a.get("status") in (1, "focus", "just_focus")
        color = (80, 220, 80) if focused else (255, 255, 255)
        if a.get("select") == 0:
            color = (150, 150, 150)
        cv2.rectangle(frame, (x, y), (x2, y2), color, 2 if a.get("select") else 1)
    return frame


def focus_peaking(frame: np.ndarray, color=(0, 0, 255), sensitivity: float = 0.25) -> np.ndarray:
    """Tint the sharpest edges. sensitivity: fraction of the frame's max edge strength."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = np.abs(cv2.Laplacian(cv2.GaussianBlur(gray, (3, 3), 0), cv2.CV_32F, ksize=3))
    mask = edges > max(edges.max() * sensitivity, 12)
    mask = cv2.dilate(mask.astype(np.uint8), np.ones((2, 2), np.uint8)).astype(bool)
    frame[mask] = color
    return frame


def zebras(frame: np.ndarray, threshold: int = 242, phase: int = 0) -> np.ndarray:
    """Diagonal stripes over areas brighter than `threshold` (0-255; 242 ≈ 95%)."""
    h, w = frame.shape[:2]
    over = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY) >= threshold
    yy, xx = np.indices((h, w))
    stripes = ((xx + yy + phase) // 6) % 2 == 0
    m = over & stripes
    frame[m] = (frame[m] // 3)
    return frame
