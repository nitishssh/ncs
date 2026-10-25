"""USB control through Canon's EDSDK - the protocol EOS Utility uses, which exposes more of the camera than
the Wi-Fi CCAPI. Needs Canon's EDSDK library (free, not redistributable): see docs/EDSDK_TODO.md.

All SDK calls run on one dedicated thread (the SDK expects that; the hub calls from several threads), which
also pumps the SDK's event queue. Values use the same raw strings as the CCAPI transport ("1/125", "f4.0"),
so labels, presets and the web app work unchanged."""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path

from studio_utility import edsdk_codes as codes
from studio_utility.core import Cancelled, CameraInfo, NotSupported, Transport

SETTINGS = ("mode", "iso", "tv", "av", "exposure", "wb", "picture_style")
# Property ID ranges scanned by discover(): general, image, capture, live view, and the newer movie/extended IDs
DISCOVER_RANGES = [(0x0000, 0x0030), (0x0100, 0x0140), (0x0400, 0x0450), (0x0500, 0x0560),
                   (0x01000000, 0x01000500)]



def spin_run_loop(seconds: float) -> None:
    """Turn this thread's macOS run loop: Canon's SDK only sees USB cameras while it turns."""
    import ctypes
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFRunLoopRunInMode.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_bool]
    mode = ctypes.c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        cf.CFRunLoopRunInMode(mode, 0.02, False)


class SdkThread:
    """Runs callables on one thread; pumps SDK events between jobs."""

    def __init__(self, pump=None):
        self.jobs: queue.Queue = queue.Queue()
        self.pump = pump
        self.stopping = False
        self.thread = threading.Thread(target=self._loop, daemon=True, name="edsdk")
        self.thread.start()

    def _loop(self):
        while not self.stopping:
            try:
                fn, args, box, done = self.jobs.get(timeout=0.05)
            except queue.Empty:
                if self.pump:
                    try:
                        self.pump()
                    except Exception:
                        pass
                continue
            try:
                box["result"] = fn(*args)
            except BaseException as e:  # re-raised in the caller's thread
                box["error"] = e
            done.set()

    def call(self, fn, *args, timeout: float = 30):
        box, done = {}, threading.Event()
        self.jobs.put((fn, args, box, done))
        if not done.wait(timeout):
            raise TimeoutError(f"camera did not answer within {timeout:.0f} s")
        if "error" in box:
            raise box["error"]
        return box.get("result")

    def stop(self):
        self.stopping = True
        self.thread.join(2)


class MainThreadRunner:
    """macOS: Canon's SDK only finds cameras when called on the main thread, so jobs run inline there (one at a
    time) and the run loop turns between them. Used by the CLI; same interface as SdkThread."""

    def __init__(self, pump=None):
        self.pump = pump
        self.lock = threading.RLock()

    def call(self, fn, *args, timeout: float = 30):
        with self.lock:
            spin_run_loop(0.02)
            if self.pump:
                try:
                    self.pump()
                except Exception:
                    pass
            return fn(*args)

    def stop(self):
        pass


class EDSDKTransport(Transport):
    name = "usb-edsdk"

    def __init__(self, sdk_factory=None, lib_path: str | None = None):
        """sdk_factory() -> object with edsdk_native.Sdk's methods (tests pass a fake)."""
        self._real_sdk = sdk_factory is None
        if sdk_factory is None:
            from studio_utility.edsdk_native import Sdk
            sdk_factory = lambda: Sdk(lib_path)  # noqa: E731
        self.sdk_factory = sdk_factory
        self.sdk = None
        self.cam = None
        self.worker: SdkThread | None = None
        self.items: dict[str, int] = {}         # "edsdk:/DCIM/100CANON/IMG_0001.JPG" -> directory item ref
        self._changes: set[int] = set()         # property IDs changed since the last events() call
        self._added: list[str] = []
        self._lost = False
        self._evf_on = False
        self._last_frame: bytes | None = None
        self._rec_since: float | None = None
        self._cv = threading.Condition()

    # ---- lifecycle
    def _run(self, fn, *args, timeout: float = 30):
        if not self.worker:
            raise RuntimeError("not connected")
        return self.worker.call(fn, *args, timeout=timeout)

    def connect(self) -> None:
        on_mac_main = sys.platform == "darwin" and threading.current_thread() is threading.main_thread()
        self.worker = MainThreadRunner() if on_mac_main and self._real_sdk else SdkThread()
        try:
            self._run(self._connect)
        except Exception:
            self.worker.stop()
            self.worker = None
            raise
        self.worker.pump = self.sdk.pump

    def _connect(self):
        self.sdk = self.sdk_factory()
        self.sdk.initialize()
        cams = self.sdk.cameras()
        for _ in range(30 if sys.platform == "darwin" else 0):  # macOS reports USB cameras a moment later
            if cams:
                break
            spin_run_loop(0.1)
            cams = self.sdk.cameras()
        if not cams:
            self.sdk.terminate()
            raise RuntimeError("no camera on USB: set the camera's USB connection app to "
                               "'Photo Import/Remote Control' and close EOS Utility")
        self.cam = cams[0][0]
        for ref, _ in cams[1:]:
            self.sdk.release(ref)
        self.sdk.open(self.cam)
        try:  # shots stay on the card (not the PC) so the Files panel and auto-import see them
            self.sdk.set_property(self.cam, codes.PROP["save_to"], codes.SAVE_TO_CAMERA)
        except Exception:
            pass
        self.sdk.on_property(self.cam, self._on_property)
        self.sdk.on_object(self.cam, self._on_object)
        self.sdk.on_state(self.cam, self._on_state)

    def disconnect(self) -> None:
        if not self.worker:
            return
        try:
            self._run(self._disconnect, timeout=10)
        finally:
            self.worker.stop()
            self.worker = None

    def _disconnect(self):
        for ref in self.items.values():
            self.sdk.release(ref)
        self.items.clear()
        if self.cam:
            self.sdk.close(self.cam)
            self.sdk.release(self.cam)
            self.cam = None
        self.sdk.terminate()

    # ---- SDK callbacks (run on the SDK thread during pump)
    def _on_property(self, event, pid, param):
        if event == codes.PROPERTY_EVENT_CHANGED or event == codes.PROPERTY_EVENT_DESC_CHANGED:
            with self._cv:
                self._changes.add(pid)
                self._cv.notify_all()

    def _on_object(self, event, ref):
        if event in (codes.OBJECT_EVENT_DIR_ITEM_CREATED, codes.OBJECT_EVENT_DIR_ITEM_REQUEST_TRANSFER) and ref:
            try:
                name = self.sdk.item_info(ref)["name"]
            except Exception:
                return
            path = f"edsdk:/new/{name}"
            self.items[path] = ref
            with self._cv:
                self._added.append(path)
                self._cv.notify_all()

    def _on_state(self, event, data):
        if event == codes.STATE_EVENT_SHUTDOWN:
            with self._cv:
                self._lost = True
                self._cv.notify_all()

    # ---- info / settings
    def info(self) -> CameraInfo:
        def read():
            name = self.sdk.get_property(self.cam, codes.PROP["product_name"])
            try:
                fw = self.sdk.get_property(self.cam, codes.PROP["firmware"])
            except Exception:
                fw = ""
            return name, fw
        name, fw = self._run(read)
        return CameraInfo(model=f"{name} (USB)", firmware=str(fw))

    def capabilities(self) -> set[str]:
        return super().capabilities() - {"zoom", "zoom_move", "focus_at", "liveview_info"} | {"events", "discover"}

    def _setting(self, key: str) -> dict:
        pid = codes.PROP[key]
        code = self.sdk.get_property(self.cam, pid)
        try:
            options = self.sdk.property_options(self.cam, pid)
        except Exception:
            options = []
        return {"value": codes.to_value(key, code), "ability": [codes.to_value(key, c) for c in options]}

    def get_setting(self, key: str) -> dict:
        if key not in SETTINGS:
            raise NotSupported(f"{key} over USB")
        return self._run(self._setting, key)

    def set_setting(self, key: str, value: str) -> None:
        if key not in SETTINGS:
            raise NotSupported(f"{key} over USB")
        self._run(self.sdk.set_property, self.cam, codes.PROP[key], codes.to_code(key, value))

    # ---- shooting
    def shoot(self, autofocus: bool = True) -> None:
        def go():
            if autofocus:
                self.sdk.command(self.cam, codes.CMD_PRESS_SHUTTER, codes.SHUTTER_FULL)
                self.sdk.command(self.cam, codes.CMD_PRESS_SHUTTER, codes.SHUTTER_OFF)
            else:
                self.sdk.command(self.cam, codes.CMD_TAKE_PICTURE, 0)
        self._run(go)

    def half_press(self) -> None:
        def go():
            self.sdk.command(self.cam, codes.CMD_PRESS_SHUTTER, codes.SHUTTER_HALF)
            self.sdk.command(self.cam, codes.CMD_PRESS_SHUTTER, codes.SHUTTER_OFF)
        self._run(go)

    def record(self, start: bool) -> None:
        self._run(self.sdk.set_property, self.cam, codes.PROP["record"],
                  codes.RECORD_START if start else codes.RECORD_STOP)
        self._rec_since = time.time() if start else None

    # ---- live view
    def _frame(self) -> bytes:
        if not self._evf_on:
            current = self.sdk.get_property(self.cam, codes.PROP["evf_output"])
            self.sdk.set_property(self.cam, codes.PROP["evf_output"], int(current) | codes.EVF_OUTPUT_PC)
            self._evf_on = True
        for _ in range(20):  # the first frames after switching live view on are "not ready"
            try:
                return self.sdk.download_evf(self.cam)
            except Exception as e:
                if getattr(e, "code", None) not in (codes.ERR_OBJECT_NOTREADY, codes.ERR_DEVICE_BUSY):
                    raise
                if self._last_frame:
                    return self._last_frame
                time.sleep(0.05)
        raise RuntimeError("live view didn't start")

    def liveview_frame(self) -> bytes:
        self._last_frame = self._run(self._frame)
        return self._last_frame

    # ---- status
    def status(self) -> dict:
        def read():
            out = {"temp": "normal",
                   "recording": int(time.time() - self._rec_since) if self._rec_since else None}
            try:
                out["battery"] = codes.battery_percent(int(self.sdk.get_property(self.cam, codes.PROP["battery"])))
            except Exception:
                out["battery"] = None
            try:
                out["shots_left"] = int(self.sdk.get_property(self.cam, codes.PROP["shots_left"]))
            except Exception:
                pass
            return out
        return self._run(read)

    # ---- files
    def _walk(self, ref, prefix: str, out: list[str]):
        for child in self.sdk.children(ref):
            info = self.sdk.item_info(child)
            path = f"{prefix}/{info['name']}"
            if info["folder"]:
                self._walk(child, path, out)
                self.sdk.release(child)
            else:
                self.items[path] = child
                out.append(path)

    def list_files(self) -> list[str]:
        def walk():
            for ref in self.items.values():
                self.sdk.release(ref)
            self.items.clear()
            out: list[str] = []
            for vol in self.sdk.children(self.cam):  # memory cards
                self._walk(vol, "edsdk:", out)
                self.sdk.release(vol)
            return sorted(out)
        return self._run(walk, timeout=120)

    def _item(self, remote: str):
        if remote not in self.items:
            self.list_files()
        if remote not in self.items:
            raise FileNotFoundError(remote)
        return self.items[remote]

    def download(self, remote: str, dest: Path, progress=None, cancel=None) -> Path:
        item = self._item(remote)
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / remote.rsplit("/", 1)[-1]
        part = out.with_name(out.name + ".part")

        def go():
            size = self.sdk.item_info(item)["size"]
            return self.sdk.download(item, part, size, progress=progress, cancel=cancel), size
        ok, size = self._run(go, timeout=3600)
        if not ok:
            part.unlink(missing_ok=True)
            raise Cancelled(remote)
        if part.stat().st_size != size:
            part.unlink(missing_ok=True)
            raise IOError(f"incomplete download: {part.stat().st_size} of {size} bytes")
        part.replace(out)
        return out

    def thumbnail(self, remote: str) -> bytes:
        return self._run(self.sdk.thumbnail, self._item(remote))

    # ---- events (same normalized shape as CCAPITransport.events)
    def events(self, timeout: float = 10) -> dict:
        with self._cv:
            self._cv.wait_for(lambda: self._changes or self._added or self._lost, timeout)
            changes, self._changes = self._changes, set()
            added, self._added = self._added, []
            lost, self._lost = self._lost, False
        out: dict = {}
        if added:
            out["added"] = added
        if lost:
            out["camera_lost"] = "camera turned off or unplugged"
        by_pid = {codes.PROP[k]: k for k in SETTINGS}
        keys = [by_pid[p] for p in changes if p in by_pid]
        if keys:
            out["settings"] = {k: self.get_setting(k) for k in keys}
        if codes.PROP["battery"] in changes or codes.PROP["shots_left"] in changes:
            out["status"] = self.status()
        return out

    def wait_new_files(self, timeout: float = 30) -> list[str]:
        return self.events(timeout).get("added", [])

    # ---- discovery: what can this body do over USB?
    def discover(self) -> list[dict]:
        """Every property ID the camera answers, with its value and how many options it offers.
        This is how we learn whether e.g. Canon Log or the movie format are remotely settable."""
        names = {v: k for k, v in codes.PROP.items()}

        def scan():
            found = []
            for lo, hi in DISCOVER_RANGES:
                for pid in range(lo, hi):
                    try:
                        value = self.sdk.get_property(self.cam, pid)
                    except Exception:
                        continue
                    try:
                        options = self.sdk.property_options(self.cam, pid)
                    except Exception:
                        options = []
                    shown = value if isinstance(value, (str, int)) else f"<{len(value)} bytes>"
                    found.append({"id": f"0x{pid:08X}", "name": names.get(pid, ""), "value": shown,
                                  "options": len(options)})
            return found
        return self._run(scan, timeout=300)
