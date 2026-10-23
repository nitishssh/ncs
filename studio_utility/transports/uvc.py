"""Video via UVC webcam mode or an HDMI capture dongle. OpenCV, macOS + Windows."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import cv2
import numpy as np

from studio_utility.core import CameraInfo, Transport


_rife = None


def _gpu_interpolate(a, b, t: float):
    """RIFE on the GPU (Vulkan: NVIDIA RTX, AMD, Intel, Apple via MoltenVK). None if unavailable."""
    global _rife
    if _rife is False:
        return None
    try:
        if _rife is None:
            from rife_ncnn_vulkan_python import Rife
            _rife = Rife(gpuid=0)
        from PIL import Image
        rgb = lambda f: Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
        out = _rife.process(rgb(a), rgb(b), timestep=t)
        return cv2.cvtColor(np.asarray(out), cv2.COLOR_RGB2BGR)
    except Exception:
        _rife = False
        return None


def interpolate(a, b, t: float = 0.5):
    """Synthesize the frame at time t between a and b: RIFE on the GPU if installed, else optical flow."""
    mid = _gpu_interpolate(a, b, t)
    return mid if mid is not None else _flow_interpolate(a, b, t)


def _flow_interpolate(a, b, t: float = 0.5):
    """Synthesize the frame at time t between a and b using dense optical flow."""
    small = lambda f: cv2.cvtColor(cv2.resize(f, None, fx=0.5, fy=0.5), cv2.COLOR_BGR2GRAY)
    flow = cv2.calcOpticalFlowFarneback(small(a), small(b), None, 0.5, 3, 15, 3, 5, 1.2, 0)
    h, w = a.shape[:2]
    flow = cv2.resize(flow, (w, h)) * 2
    gx, gy = np.meshgrid(np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32))
    from_a = cv2.remap(a, gx - flow[..., 0] * t, gy - flow[..., 1] * t, cv2.INTER_LINEAR)
    from_b = cv2.remap(b, gx + flow[..., 0] * (1 - t), gy + flow[..., 1] * (1 - t), cv2.INTER_LINEAR)
    return cv2.addWeighted(from_a, 1 - t, from_b, t, 0)


class UVCTransport(Transport):
    name = "video"

    def __init__(self, index: int = 0, width: int = 1920, height: int = 1080):
        self.index, self.size = index, (width, height)
        self.cap: cv2.VideoCapture | None = None
        self.writer: cv2.VideoWriter | None = None
        self.framegen = False  # insert one synthesized frame between each captured pair
        self._prev = None
        self._pending = None

    def connect(self) -> None:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_AVFOUNDATION
        self.cap = cv2.VideoCapture(self.index, backend)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.size[0])
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.size[1])
        if not self.cap.isOpened():
            hint = (" On macOS, allow Camera access for this app in System Settings > "
                    "Privacy & Security > Camera, then restart it.") if sys.platform == "darwin" else ""
            raise RuntimeError(f"Could not open video device {self.index}.{hint}")

    def disconnect(self) -> None:
        self.record(False)
        if self.cap:
            self.cap.release()
            self.cap = None

    def info(self) -> CameraInfo:
        return CameraInfo(model=f"UVC device #{self.index}")

    def _read(self):
        ok, frame = self.cap.read()
        if not ok:
            raise RuntimeError("frame grab failed")
        mid = interpolate(self._prev, frame) if self.framegen and self._prev is not None else None
        self._prev = frame
        if self.writer:
            if mid is not None:
                self.writer.write(mid)
            self.writer.write(frame)
        return frame, mid

    def liveview_frame(self) -> bytes:
        if self._pending is not None:
            frame, self._pending = self._pending, None
        else:
            frame, mid = self._read()
            if mid is not None:
                frame, self._pending = mid, frame
        return cv2.imencode(".jpg", frame)[1].tobytes()

    def shoot(self, autofocus: bool = True) -> None:
        out = Path("captures") / f"still_{int(time.time())}.jpg"
        out.parent.mkdir(exist_ok=True)
        cv2.imwrite(str(out), self._read()[0])

    def record(self, start: bool) -> None:
        if start and not self.writer:
            Path("captures").mkdir(exist_ok=True)
            w, h = int(self.cap.get(3)), int(self.cap.get(4))
            fps = 60 if self.framegen else 30
            self.writer = cv2.VideoWriter(f"captures/video_{int(time.time())}.mp4",
                                          cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
        elif not start and self.writer:
            self.writer.release()
            self.writer = None
