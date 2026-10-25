"""Shared camera interface every transport implements."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


class Cancelled(Exception):
    """A long operation (download) was cancelled by the user."""


class CameraLost(RuntimeError):
    """The camera session ended (turned off, unplugged, or it reset the link when its mode dial moved)."""


class NotSupported(Exception):
    """Raised when a transport cannot do the requested operation."""


@dataclass
class CameraInfo:
    model: str = "unknown"
    serial: str = ""
    firmware: str = ""
    extra: dict = field(default_factory=dict)


class Transport(ABC):
    name: str = "base"

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def info(self) -> CameraInfo: ...

    @abstractmethod
    def shoot(self, autofocus: bool = True) -> None: ...

    def get_setting(self, key: str) -> dict:
        raise NotSupported(f"{self.name}: get_setting")

    def set_setting(self, key: str, value: str) -> None:
        raise NotSupported(f"{self.name}: set_setting")

    def liveview_frame(self) -> bytes:
        """Return one JPEG frame."""
        raise NotSupported(f"{self.name}: liveview")

    def list_files(self) -> list[str]:
        raise NotSupported(f"{self.name}: list_files")

    def download(self, remote: str, dest: Path, progress=None, cancel=None) -> Path:
        """Copy the original file byte-for-byte. progress(done, total) is called per chunk;
        cancel() returning True aborts and removes the partial file (raises Cancelled)."""
        raise NotSupported(f"{self.name}: download")

    def thumbnail(self, remote: str) -> bytes:
        """Small JPEG preview of a photo, RAW or movie on the card."""
        raise NotSupported(f"{self.name}: thumbnail")

    def events(self, timeout: float = 10) -> dict:
        """Block up to `timeout` s for camera changes. Normalized:
        {"added": [paths], "settings": {key: {"value", "ability"}}, "status": {...},
         "effective": {"tv", "av", "iso"}}  (keys present only when something changed).
        The camera has ONE event queue: only one consumer (the UI's EventHub) may call this."""
        raise NotSupported(f"{self.name}: events")

    def wait_new_files(self, timeout: float = 30) -> list[str]:
        """Block up to `timeout` seconds; return card paths added since the last call."""
        raise NotSupported(f"{self.name}: wait_new_files")

    def record(self, start: bool) -> None:
        raise NotSupported(f"{self.name}: record")

    def status(self) -> dict:
        """battery (%), card_free_gb, recording (seconds or None), temp ('normal'/'warning')."""
        raise NotSupported(f"{self.name}: status")

    def liveview_info(self) -> dict:
        """{'histogram': 4x256 (Y,R,G,B), 'afframe': [...], 'image': live view area in sensor pixels}."""
        raise NotSupported(f"{self.name}: liveview_info")

    def zoom(self, position: int) -> None:
        """Power zoom, 0 = wide .. 100 = tele."""
        raise NotSupported(f"{self.name}: zoom")

    def zoom_move(self, direction: str) -> None:
        """Continuous power zoom: 'wide' or 'tele' starts moving, 'stop' stops."""
        raise NotSupported(f"{self.name}: zoom_move")

    def focus_at(self, x: float, y: float) -> None:
        """Move the AF point to normalized live view coordinates (0..1) and focus."""
        raise NotSupported(f"{self.name}: focus_at")

    def half_press(self) -> None:
        """Autofocus without releasing the shutter."""
        raise NotSupported(f"{self.name}: half_press")

    def focus_drive(self, step: int) -> None:
        """Nudge manual focus: -3..-1 nearer, 1..3 farther (bigger steps are coarser)."""
        raise NotSupported(f"{self.name}: focus_drive")

    def capabilities(self) -> set[str]:
        """Features this transport implements, so the UI can hide the rest."""
        feats = {"get_setting", "set_setting", "liveview_frame", "list_files", "download", "record",
                 "status", "zoom", "zoom_move", "focus_at", "half_press", "focus_drive", "liveview_info", "thumbnail", "wait_new_files", "events"}
        return {f for f in feats if getattr(type(self), f) is not getattr(Transport, f)}
