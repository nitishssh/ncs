"""USB fallback via libgphoto2 (best on macOS/Linux). Install: brew install gphoto2 && pip install gphoto2.

On macOS run `killall PTPCamera` first - the system grabs the camera on plug-in.
"""
from __future__ import annotations

from pathlib import Path

import gphoto2 as gp

from camctl.core import CameraInfo, Transport

KEYS = {"iso": "iso", "av": "aperture", "tv": "shutterspeed", "wb": "whitebalance", "mode": "autoexposuremode"}


class GPhotoTransport(Transport):
    name = "usb-gphoto"

    def __init__(self):
        self.cam = gp.Camera()
        self.last_capture: str | None = None

    def connect(self) -> None:
        self.cam.init()

    def disconnect(self) -> None:
        self.cam.exit()

    def info(self) -> CameraInfo:
        return CameraInfo(model=str(self.cam.get_summary()).splitlines()[0])

    def shoot(self, autofocus: bool = True) -> None:
        p = self.cam.capture(gp.GP_CAPTURE_IMAGE)
        self.last_capture = f"{p.folder}/{p.name}"

    def get_setting(self, key: str) -> dict:
        w = self.cam.get_single_config(KEYS.get(key, key))
        return {"value": w.get_value(), "ability": [w.get_choice(i) for i in range(w.count_choices())]}

    def set_setting(self, key: str, value: str) -> None:
        cfg = self.cam.get_config()
        cfg.get_child_by_name(KEYS.get(key, key)).set_value(value)
        self.cam.set_config(cfg)

    def liveview_frame(self) -> bytes:
        return bytes(self.cam.capture_preview().get_data_and_size())

    def list_files(self) -> list[str]:
        out: list[str] = []

        def walk(folder: str):
            for name, _ in self.cam.folder_list_files(folder):
                out.append(f"{folder}/{name}")
            for name, _ in self.cam.folder_list_folders(folder):
                walk(f"{folder.rstrip('/')}/{name}")
        walk("/")
        return out

    def download(self, remote: str, dest: Path) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        folder, name = remote.rsplit("/", 1)
        out = dest / name
        self.cam.file_get(folder or "/", name, gp.GP_FILE_TYPE_NORMAL).save(str(out))
        return out
