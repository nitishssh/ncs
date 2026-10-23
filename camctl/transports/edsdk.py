"""USB tethering via Canon EDSDK (ctypes). The most reliable USB path on macOS and Windows.

EDSDK is free but must be downloaded from Canon's developer portal. Point CAMCTL_EDSDK
at the library: EDSDK.dll (Windows) or EDSDK.framework/EDSDK (macOS).
"""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

from camctl.core import CameraInfo, Transport

EDS_ERR_OK = 0
kEdsCameraCommand_TakePicture = 0x00000000
kEdsCameraCommand_PressShutterButton = 0x00000004
kEdsShutterButton_OFF, kEdsShutterButton_Completely = 0, 3
# Property IDs from EDSDKTypes.h
PROPS = {"iso": 0x00000402, "av": 0x00000405, "tv": 0x00000406, "wb": 0x00000106, "mode": 0x00000400}


def _default_lib() -> str:
    if sys.platform == "win32":
        return "EDSDK.dll"
    return str(Path.home() / "Library/Frameworks/EDSDK.framework/EDSDK")


class EDSDKError(RuntimeError):
    pass


class EDSDKTransport(Transport):
    name = "usb-edsdk"

    def __init__(self, lib_path: str | None = None):
        path = lib_path or os.environ.get("CAMCTL_EDSDK") or _default_lib()
        loader = ctypes.WinDLL if sys.platform == "win32" else ctypes.CDLL
        try:
            self.sdk = loader(path)
        except OSError as e:
            raise EDSDKError(f"EDSDK not found at {path}. Download it from Canon and set CAMCTL_EDSDK.") from e
        self.cam = ctypes.c_void_p()

    def _ok(self, err: int, what: str) -> None:
        if err != EDS_ERR_OK:
            raise EDSDKError(f"{what} failed: 0x{err:08X}")

    def connect(self) -> None:
        s = self.sdk
        self._ok(s.EdsInitializeSDK(), "InitializeSDK")
        cams, count = ctypes.c_void_p(), ctypes.c_uint32()
        self._ok(s.EdsGetCameraList(ctypes.byref(cams)), "GetCameraList")
        self._ok(s.EdsGetChildCount(cams, ctypes.byref(count)), "GetChildCount")
        if count.value == 0:
            raise EDSDKError("No camera on USB")
        self._ok(s.EdsGetChildAtIndex(cams, 0, ctypes.byref(self.cam)), "GetChildAtIndex")
        s.EdsRelease(cams)
        self._ok(s.EdsOpenSession(self.cam), "OpenSession")

    def disconnect(self) -> None:
        if self.cam:
            self.sdk.EdsCloseSession(self.cam)
            self.sdk.EdsRelease(self.cam)
            self.cam = ctypes.c_void_p()
        self.sdk.EdsTerminateSDK()

    def info(self) -> CameraInfo:
        # kEdsPropID_ProductName = 0x00000002
        buf = ctypes.create_string_buffer(256)
        self._ok(self.sdk.EdsGetPropertyData(self.cam, 0x2, 0, 256, buf), "GetProductName")
        return CameraInfo(model=buf.value.decode(errors="replace"))

    def shoot(self, autofocus: bool = True) -> None:
        if autofocus:
            self._ok(self.sdk.EdsSendCommand(self.cam, kEdsCameraCommand_PressShutterButton,
                                             kEdsShutterButton_Completely), "PressShutter")
            self._ok(self.sdk.EdsSendCommand(self.cam, kEdsCameraCommand_PressShutterButton,
                                             kEdsShutterButton_OFF), "ReleaseShutter")
        else:
            self._ok(self.sdk.EdsSendCommand(self.cam, kEdsCameraCommand_TakePicture, 0), "TakePicture")

    def get_setting(self, key: str) -> dict:
        val = ctypes.c_uint32()
        self._ok(self.sdk.EdsGetPropertyData(self.cam, PROPS[key], 0, 4, ctypes.byref(val)), f"Get {key}")
        return {"value": hex(val.value), "note": "raw EDSDK code; see EDSDKTypes.h tables"}

    def set_setting(self, key: str, value: str) -> None:
        val = ctypes.c_uint32(int(value, 0))
        self._ok(self.sdk.EdsSetPropertyData(self.cam, PROPS[key], 0, 4, ctypes.byref(val)), f"Set {key}")
