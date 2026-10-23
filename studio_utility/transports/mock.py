"""Fake R50 V for developing without hardware. Behaves like CCAPI: settings with ability lists,
animated live view, power zoom (RF-S 14-30mm PZ), touch AF, movie settings, battery/card status."""
from __future__ import annotations

import time
from pathlib import Path

import cv2
import numpy as np

from studio_utility.core import Cancelled, CameraInfo, Transport

# Values follow the R50 V manual (docs/R50V_FEATURES.md)
ABILITY = {
    "mode": ["P", "Tv", "Av", "M", "Auto"],
    "iso": ["auto", "100", "200", "400", "800", "1600", "3200", "6400", "12800"],
    "av": ["f1.8", "f2.8", "f4.0", "f5.6", "f8.0", "f11", "f16"],
    "tv": ["1/4000", "1/1000", "1/250", "1/125", "1/60", "1/30", "1/15", "1\""],
    "exposure": ["-3.0", "-2_2/3", "-2_1/3", "-2.0", "-1_2/3", "-1_1/3", "-1.0", "-0_2/3", "-0_1/3", "+0.0",
                 "+0_1/3", "+0_2/3", "+1.0", "+1_1/3", "+1_2/3", "+2.0", "+2_1/3", "+2_2/3", "+3.0"],
    "wb": ["auto", "daylight", "shade", "cloudy", "tungsten", "fluorescent"],
    "movie_size": ["4K 29.97p", "4K 25.00p", "4K 23.98p", "4K Crop 59.94p", "4K Crop 50.00p",
                   "FHD 119.88p", "FHD 59.94p", "FHD 29.97p", "FHD 23.98p"],
    "movie_format": ["XF-HEVC S 4:2:2 10bit", "XF-HEVC S 4:2:0 10bit",
                     "XF-AVC S 4:2:2 10bit", "XF-AVC S 4:2:0 8bit"],
    "movie_mode": ["Auto", "Close-up demo", "Smooth skin", "Movie IS", "P", "Tv", "Av", "M", "Slow/Fast"],
    "movie_af": ["Servo AF", "One-shot"],
    "picture_style": ["Auto", "Standard", "Portrait", "Landscape", "Fine Detail", "Neutral",
                      "Faithful", "Monochrome"],
    "canon_log": ["Off", "Canon Log 3"],
    "log_assist": ["Off", "On"],
    "hdr_pq": ["Off", "On"],
}
WIDE_MM, TELE_MM = 14, 30


class MockTransport(Transport):
    name = "mock"

    def __init__(self, **_):
        self.connected = False
        self.values = {"exposure": "+0.0", "mode": "M", "iso": "400", "av": "f4.0", "tv": "1/125", "wb": "auto",
                       "movie_size": "4K 29.97p", "movie_format": "XF-HEVC S 4:2:2 10bit",
                       "movie_mode": "Auto", "movie_af": "Servo AF",
                       "picture_style": "Standard", "canon_log": "Off", "log_assist": "Off", "hdr_pq": "Off"}
        self.files: list[str] = []
        self.recording_since: float | None = None
        self.zoom_pos = 0
        self.af_point = (0.5, 0.5)
        self.af_flash = 0.0
        self.t0 = time.time()

    def _need(self):
        if not self.connected:
            raise RuntimeError("mock camera not connected")

    def connect(self) -> None:
        self.connected = True
        self.files = [f"/ccapi/ver110/contents/sd/100CANON/IMG_{n:04d}.JPG" for n in range(1, 4)]

    def disconnect(self) -> None:
        self.recording_since = None
        self.connected = False

    def info(self) -> CameraInfo:
        self._need()
        return CameraInfo("Canon EOS R50 V (mock)", "000000000000", "1.2.0", {"lens": "RF-S14-30mm F4-6.3 IS STM PZ"})

    def status(self) -> dict:
        self._need()
        up = time.time() - self.t0
        rec = int(time.time() - self.recording_since) if self.recording_since else None
        return {"battery": max(0, 87 - int(up / 30)),
                "card_free_gb": round(118.4 - len(self.files) * 0.009 - (rec or 0) * 0.016, 1),
                "recording": rec,
                "temp": "warning" if rec and rec > 600 else "normal",
                "focal_mm": self.focal_mm()}

    def focal_mm(self) -> int:
        return round(WIDE_MM + (TELE_MM - WIDE_MM) * self.zoom_pos / 100)

    def shoot(self, autofocus: bool = True) -> None:
        self._need()
        if autofocus:
            self.half_press()
        self.files.append(f"/ccapi/ver110/contents/sd/100CANON/IMG_{len(self.files) + 1:04d}.JPG")
        self._emit(added=[self.files[-1]], effective={"tv": self.values["tv"], "av": self.values["av"],
                                                      "iso": "800" if self.values["iso"] == "auto" else self.values["iso"]})

    def half_press(self) -> None:
        self._need()
        self.af_flash = time.time()

    def focus_at(self, x: float, y: float) -> None:
        self._need()
        self.af_point = (min(max(x, 0.05), 0.95), min(max(y, 0.05), 0.95))
        self.half_press()

    def zoom(self, position: int) -> None:
        self._need()
        self.zoom_pos = min(max(int(position), 0), 100)

    def zoom_move(self, direction: str) -> None:
        self._need()
        step = {"wide": -10, "tele": 10}.get(direction, 0)
        self.zoom_pos = min(max(self.zoom_pos + step, 0), 100)

    def get_setting(self, key: str) -> dict:
        self._need()
        return {"value": self.values[key], "ability": ABILITY[key]}

    def set_setting(self, key: str, value: str) -> None:
        self._need()
        if value not in ABILITY[key]:
            raise ValueError(f"{value!r} is not a valid {key}")
        if self.recording_since and key.startswith("movie_"):
            raise RuntimeError("cannot change movie settings while recording")
        self.values[key] = value
        self._emit(settings={key: {"value": value, "ability": ABILITY[key]}})

    def _frame(self) -> np.ndarray:
        t = time.time() - self.t0
        h, w = 360, 640
        x = np.linspace(0, 1, w)[None, :]
        img = np.zeros((h, w, 3), np.uint8)
        img[..., 0] = (120 + 80 * np.sin(x * 6 + t)).astype(np.uint8)
        img[..., 1] = 60
        img[..., 2] = (120 + 80 * np.cos(x * 4 - t)).astype(np.uint8)
        scale = self.focal_mm() / WIDE_MM
        cx = int(w / 2 + 160 * np.sin(t))
        cv2.circle(img, (cx, h // 2), int(40 * scale), (255, 255, 255), -1)
        label = f"MOCK  {self.focal_mm()}mm  {self.values['tv']}  {self.values['av']}  ISO {self.values['iso']}"
        cv2.putText(img, label, (16, h - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
        if self.recording_since:
            cv2.circle(img, (w - 24, 24), 9, (0, 0, 255), -1)
        return img

    def liveview_info(self) -> dict:
        self._need()
        from studio_utility.overlays import histogram_from_frame
        img = {"positionx": 0, "positiony": 0, "positionwidth": 6000, "positionheight": 3376}
        ax, ay = self.af_point
        focused = time.time() - self.af_flash < 0.8
        af = [{"x": int(ax * 6000) - 260, "y": int(ay * 3376) - 260, "width": 520, "height": 520,
               "status": 1 if focused else 0, "select": 1}]
        return {"histogram": histogram_from_frame(self._frame()), "afframe": af, "image": img}

    def liveview_frame(self) -> bytes:
        self._need()
        return cv2.imencode(".jpg", self._frame())[1].tobytes()

    def list_files(self) -> list[str]:
        self._need()
        return list(self.files)

    def download(self, remote: str, dest: Path, progress=None, cancel=None) -> Path:
        """Simulates a ~6 MB/s Wi-Fi transfer: JPEG 2 MB, RAW 30 MB, movie 60 MB."""
        self._need()
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / remote.rsplit("/", 1)[-1]
        total = {"JPG": 2, "CR3": 30, "MP4": 60}.get(remote.rsplit(".", 1)[-1], 2) * 1_000_000
        chunk = 1_000_000
        with out.open("wb") as f:
            for done in range(chunk, total + 1, chunk):
                if cancel and cancel():
                    f.close()
                    out.unlink(missing_ok=True)
                    raise Cancelled(remote)
                time.sleep(chunk / 6e6)
                f.write(b"\0" * chunk)
                if progress:
                    progress(done, total)
        return out

    def thumbnail(self, remote: str) -> bytes:
        self._need()
        name = remote.rsplit("/", 1)[-1]
        img = cv2.resize(self._frame(), (160, 90))
        cv2.putText(img, name[-8:], (6, 82), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
        return cv2.imencode(".jpg", img)[1].tobytes()

    def _emit(self, **changes) -> None:
        pending = self.__dict__.setdefault("_pending", {})
        for k, v in changes.items():
            if isinstance(v, list):
                pending.setdefault(k, []).extend(v)
            else:
                pending.setdefault(k, {}).update(v)

    def events(self, timeout: float = 10) -> dict:
        self._need()
        end = time.time() + timeout
        while self.connected and time.time() < end:
            bat = self.status()["battery"]
            if bat != getattr(self, "_last_bat", None):
                self._last_bat = bat
                self._emit(status={"battery": bat})
            if self.__dict__.get("_pending"):
                out, self._pending = self._pending, {}
                return out
            time.sleep(0.1)
        return {}

    def wait_new_files(self, timeout: float = 30) -> list[str]:
        return self.events(timeout).get("added", [])

    def record(self, start: bool) -> None:
        self._need()
        self.recording_since = time.time() if start else None
        if start:
            self.files.append(f"/ccapi/ver110/contents/sd/100CANON/MVI_{len(self.files) + 1:04d}.MP4")
            self._emit(added=[self.files[-1]])
