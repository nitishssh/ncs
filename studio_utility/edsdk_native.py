"""Thin, Pythonic ctypes layer over Canon's EDSDK (EDSDK.dll / EDSDK.framework). Nothing above this file
touches ctypes, so the transport can be tested with a fake.

EDSDK is free from Canon's developer programme, but its licence doesn't allow committing it to this repo:
put it in vendor/EDSDK/ (git-ignored) or point STUDIO_UTILITY_EDSDK at EDSDK.dll / EDSDK.framework/EDSDK.

Written against the EDSDK 13.x API (64-bit sizes for streams and directory items). UNVERIFIED until run
with the real library - see docs/EDSDK_TODO.md."""
from __future__ import annotations

import ctypes
import os
import sys
from ctypes import POINTER, byref, c_char, c_int32, c_uint32, c_uint64, c_void_p
from pathlib import Path

from studio_utility import edsdk_codes as codes

ROOT = Path(__file__).resolve().parent.parent
WIN = sys.platform == "win32"
FUNC = ctypes.WINFUNCTYPE if WIN else ctypes.CFUNCTYPE   # EDSDK callbacks are stdcall on Windows

# EdsDataType values we decode; everything else comes back as raw bytes
T_BOOL, T_STRING, T_INT32, T_UINT32 = 1, 2, 8, 9


class EdsError(RuntimeError):
    def __init__(self, what: str, code: int):
        super().__init__(f"{what}: {codes.error_text(code)}")
        self.code = code


class DeviceInfo(ctypes.Structure):
    _fields_ = [("szPortName", c_char * 256), ("szDeviceDescription", c_char * 256),
                ("deviceSubType", c_uint32), ("reserved", c_uint32)]


class PropertyDesc(ctypes.Structure):
    _fields_ = [("form", c_int32), ("access", c_int32), ("numElements", c_int32), ("propDesc", c_int32 * 128)]


class DirectoryItemInfo(ctypes.Structure):
    _fields_ = [("size", c_uint64), ("isFolder", c_int32), ("groupID", c_uint32), ("option", c_uint32),
                ("szFileName", c_char * 256), ("format", c_uint32), ("dateTime", c_uint32)]


PropertyHandler = FUNC(c_uint32, c_uint32, c_uint32, c_uint32, c_void_p)
ObjectHandler = FUNC(c_uint32, c_uint32, c_void_p, c_void_p)
StateHandler = FUNC(c_uint32, c_uint32, c_uint32, c_void_p)


def find_library() -> Path | None:
    """STUDIO_UTILITY_EDSDK, then vendor/EDSDK (as unpacked from Canon's zip), then next to the app."""
    env = os.environ.get("STUDIO_UTILITY_EDSDK") or os.environ.get("CAMCTL_EDSDK")
    if env:
        return Path(env)
    pattern = "**/EDSDK.dll" if WIN else "**/EDSDK.framework/EDSDK"
    bases = [ROOT / "vendor" / "EDSDK", Path(sys.executable).parent / "EDSDK", Path.home() / "EDSDK"]
    for base in bases:
        if base.exists():
            hits = sorted(base.glob(pattern), key=lambda p: ("64" not in str(p), len(str(p))))  # prefer 64-bit
            if hits:
                return hits[0]
    return None


class Sdk:
    def __init__(self, path: Path | str | None = None):
        path = Path(path) if path else find_library()
        if not path or not path.exists():
            raise FileNotFoundError("Canon EDSDK not found: put it in vendor/EDSDK/ or set STUDIO_UTILITY_EDSDK "
                                    "(see docs/EDSDK_TODO.md)")
        if WIN:
            os.add_dll_directory(str(path.parent))  # EdsImage.dll lives next to EDSDK.dll
        self.lib = (ctypes.WinDLL if WIN else ctypes.CDLL)(str(path))
        self.path = path
        self._keep = []  # callback objects must outlive the SDK's references to them
        self._declare()

    def _declare(self):
        L = self.lib
        for name in ("EdsInitializeSDK", "EdsTerminateSDK", "EdsGetEvent"):
            getattr(L, name).restype = c_uint32
        sigs = {
            "EdsGetCameraList": [POINTER(c_void_p)],
            "EdsGetChildCount": [c_void_p, POINTER(c_uint32)],
            "EdsGetChildAtIndex": [c_void_p, c_int32, POINTER(c_void_p)],
            "EdsGetDeviceInfo": [c_void_p, POINTER(DeviceInfo)],
            "EdsOpenSession": [c_void_p], "EdsCloseSession": [c_void_p], "EdsRelease": [c_void_p],
            "EdsGetPropertySize": [c_void_p, c_uint32, c_int32, POINTER(c_uint32), POINTER(c_uint32)],
            "EdsGetPropertyData": [c_void_p, c_uint32, c_int32, c_uint32, c_void_p],
            "EdsSetPropertyData": [c_void_p, c_uint32, c_int32, c_uint32, c_void_p],
            "EdsGetPropertyDesc": [c_void_p, c_uint32, POINTER(PropertyDesc)],
            "EdsSendCommand": [c_void_p, c_uint32, c_int32],
            "EdsSendStatusCommand": [c_void_p, c_uint32, c_int32],
            "EdsCreateMemoryStream": [c_uint64, POINTER(c_void_p)],
            "EdsCreateFileStream": [ctypes.c_char_p, c_uint32, c_uint32, POINTER(c_void_p)],
            "EdsGetPointer": [c_void_p, POINTER(c_void_p)],
            "EdsGetLength": [c_void_p, POINTER(c_uint64)],
            "EdsCreateEvfImageRef": [c_void_p, POINTER(c_void_p)],
            "EdsDownloadEvfImage": [c_void_p, c_void_p],
            "EdsGetDirectoryItemInfo": [c_void_p, POINTER(DirectoryItemInfo)],
            "EdsDownload": [c_void_p, c_uint64, c_void_p],
            "EdsDownloadComplete": [c_void_p], "EdsDownloadCancel": [c_void_p],
            "EdsDownloadThumbnail": [c_void_p, c_void_p],
            "EdsSetPropertyEventHandler": [c_void_p, c_uint32, PropertyHandler, c_void_p],
            "EdsSetObjectEventHandler": [c_void_p, c_uint32, ObjectHandler, c_void_p],
            "EdsSetCameraStateEventHandler": [c_void_p, c_uint32, StateHandler, c_void_p],
        }
        for name, args in sigs.items():
            fn = getattr(L, name)
            fn.argtypes, fn.restype = args, c_uint32

    def _ok(self, err: int, what: str):
        if err:
            raise EdsError(what, err)

    # lifecycle
    def initialize(self):
        self._ok(self.lib.EdsInitializeSDK(), "initialize")

    def terminate(self):
        self.lib.EdsTerminateSDK()

    def pump(self):
        """Deliver queued camera events to the handlers (call regularly from the SDK thread)."""
        self.lib.EdsGetEvent()

    def release(self, ref):
        if ref:
            self.lib.EdsRelease(ref)

    # cameras
    def cameras(self) -> list[tuple[int, str]]:
        lst = c_void_p()
        self._ok(self.lib.EdsGetCameraList(byref(lst)), "camera list")
        try:
            out = []
            for ref in self.children(lst):
                info = DeviceInfo()
                self._ok(self.lib.EdsGetDeviceInfo(ref, byref(info)), "device info")
                out.append((ref, info.szDeviceDescription.decode(errors="replace")))
            return out
        finally:
            self.release(lst)

    def open(self, cam):
        self._ok(self.lib.EdsOpenSession(cam), "open session")

    def close(self, cam):
        self.lib.EdsCloseSession(cam)

    # properties
    def get_property(self, ref, pid: int, param: int = 0):
        dtype, size = c_uint32(), c_uint32()
        self._ok(self.lib.EdsGetPropertySize(ref, pid, param, byref(dtype), byref(size)), f"property 0x{pid:X} size")
        buf = ctypes.create_string_buffer(max(size.value, 4))
        self._ok(self.lib.EdsGetPropertyData(ref, pid, param, size.value, buf), f"property 0x{pid:X}")
        if dtype.value == T_STRING:
            return buf.value.decode(errors="replace")
        if dtype.value in (T_INT32, T_UINT32, T_BOOL) and size.value == 4:
            return c_uint32.from_buffer_copy(buf.raw[:4]).value
        return bytes(buf.raw[:size.value])

    def property_options(self, ref, pid: int) -> list[int]:
        desc = PropertyDesc()
        self._ok(self.lib.EdsGetPropertyDesc(ref, pid, byref(desc)), f"property 0x{pid:X} options")
        return [desc.propDesc[i] & 0xFFFFFFFF for i in range(desc.numElements)]

    def set_property(self, ref, pid: int, value: int, param: int = 0):
        v = c_uint32(value)
        self._ok(self.lib.EdsSetPropertyData(ref, pid, param, 4, byref(v)), f"set property 0x{pid:X}")

    def command(self, cam, cmd: int, param: int = 0):
        self._ok(self.lib.EdsSendCommand(cam, cmd, param), f"command 0x{cmd:X}")

    # live view
    def download_evf(self, cam) -> bytes:
        stream, evf = c_void_p(), c_void_p()
        self._ok(self.lib.EdsCreateMemoryStream(0, byref(stream)), "memory stream")
        try:
            self._ok(self.lib.EdsCreateEvfImageRef(stream, byref(evf)), "live view image")
            self._ok(self.lib.EdsDownloadEvfImage(cam, evf), "live view frame")
            return self._stream_bytes(stream)
        finally:
            self.release(evf)
            self.release(stream)

    def _stream_bytes(self, stream) -> bytes:
        ptr, length = c_void_p(), c_uint64()
        self._ok(self.lib.EdsGetPointer(stream, byref(ptr)), "stream pointer")
        self._ok(self.lib.EdsGetLength(stream, byref(length)), "stream length")
        return ctypes.string_at(ptr, length.value)

    # files
    def children(self, ref) -> list:
        n = c_uint32()
        self._ok(self.lib.EdsGetChildCount(ref, byref(n)), "child count")
        out = []
        for i in range(n.value):
            child = c_void_p()
            self._ok(self.lib.EdsGetChildAtIndex(ref, i, byref(child)), "child")
            out.append(child.value)
        return out

    def item_info(self, item) -> dict:
        info = DirectoryItemInfo()
        self._ok(self.lib.EdsGetDirectoryItemInfo(item, byref(info)), "item info")
        return {"name": info.szFileName.decode(errors="replace"), "size": info.size, "folder": bool(info.isFolder)}

    def download(self, item, path: Path, size: int, progress=None, cancel=None, chunk: int = 1 << 20) -> bool:
        """Chunked so the UI gets progress and can cancel. Returns False if cancelled."""
        stream = c_void_p()
        self._ok(self.lib.EdsCreateFileStream(str(path).encode(), 1, 2, byref(stream)), "file stream")  # create, rw
        try:
            done = 0
            while done < size:
                if cancel and cancel():
                    self.lib.EdsDownloadCancel(item)
                    return False
                n = min(chunk, size - done)
                self._ok(self.lib.EdsDownload(item, n, stream), "download")
                done += n
                if progress:
                    progress(done, size)
            self._ok(self.lib.EdsDownloadComplete(item), "download complete")
            return True
        finally:
            self.release(stream)

    def thumbnail(self, item) -> bytes:
        stream = c_void_p()
        self._ok(self.lib.EdsCreateMemoryStream(0, byref(stream)), "memory stream")
        try:
            self._ok(self.lib.EdsDownloadThumbnail(item, stream), "thumbnail")
            return self._stream_bytes(stream)
        finally:
            self.release(stream)

    # events
    def on_property(self, cam, fn):
        cb = PropertyHandler(lambda ev, pid, param, ctx: fn(ev, pid, param) or 0)
        self._keep.append(cb)
        self._ok(self.lib.EdsSetPropertyEventHandler(cam, codes.PROPERTY_EVENT_ALL, cb, None), "property events")

    def on_object(self, cam, fn):
        cb = ObjectHandler(lambda ev, ref, ctx: fn(ev, ref) or 0)
        self._keep.append(cb)
        self._ok(self.lib.EdsSetObjectEventHandler(cam, codes.OBJECT_EVENT_ALL, cb, None), "object events")

    def on_state(self, cam, fn):
        cb = StateHandler(lambda ev, data, ctx: fn(ev, data) or 0)
        self._keep.append(cb)
        self._ok(self.lib.EdsSetCameraStateEventHandler(cam, codes.STATE_EVENT_ALL, cb, None), "state events")
