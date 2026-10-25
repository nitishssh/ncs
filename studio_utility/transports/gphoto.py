"""USB control through libgphoto2 (open source, no Canon application needed). Speaks the same PTP protocol
EOS Utility uses. Install: pip install gphoto2 (wheels bundle libgphoto2 on macOS/Linux).

macOS: the system's PTPCamera/ptpcamerad agent grabs cameras on plug-in; quit Photos/Image Capture and run
`killall PTPCamera ptpcamerad` if connecting says the device is busy. Windows: libgphoto2 needs a WinUSB driver
swap that breaks EOS Utility, so prefer the Mac (or Canon's EDSDK) there.

Values are translated to the CCAPI transport's raw strings ("1/125", "f4.0", "+0_1/3", "daylight"), so labels,
presets and the web app work unchanged; anything unrecognised passes through as gphoto2's own text."""
from __future__ import annotations

import re
import time
from pathlib import Path

from studio_utility.core import Cancelled, CameraInfo, NotSupported, Transport
from studio_utility.transports.edsdk import SdkThread

# app key -> candidate gphoto2 config names (Canon EOS driver names first)
CONFIG = {
    "iso": ["iso"], "tv": ["shutterspeed"], "av": ["aperture", "f-number"],
    "exposure": ["exposurecompensation"], "wb": ["whitebalance"], "picture_style": ["picturestyle"],
    "mode": ["autoexposuremodedial", "autoexposuremode", "expprogram"], "af_method": ["focusmode"],
    "drive": ["drivemode"],
    # USB-only: not in the camera's Wi-Fi API
    "zoom_speed": ["zoomspeed"], "color_temp": ["colortemperature"],
    "wb_shift_ab": ["whitebalanceadjusta"], "wb_shift_gm": ["whitebalanceadjustb"],
    "movie_servo_af": ["movieservoaf"],
}
WB = {"Auto": "auto", "AWB White": "awbwhite", "Daylight": "daylight", "Shadow": "shade", "Shade": "shade",
      "Cloudy": "cloudy", "Tungsten": "tungsten", "Fluorescent": "fluorescent", "Flash": "flash",
      "Manual": "custom", "Custom": "custom", "Color Temperature": "colortemp"}
MODE = {"P": "P", "TV": "Tv", "Tv": "Tv", "AV": "Av", "Av": "Av", "Manual": "M", "M": "M", "Bulb": "bulb",
        "Auto": "auto", "Custom": "C", "C1": "C1", "C2": "C2", "C3": "C3", "Movie": "movie"}
_FRACTION = {"0.3": "1/3", "0.5": "1/2", "0.7": "2/3", "0.6": "2/3"}


def to_raw(key: str, v: str) -> str:
    """gphoto2 text -> the app's raw value."""
    s = str(v).strip()
    if key == "iso":
        return "auto" if s.lower() == "auto" else s
    if key == "av" and re.fullmatch(r"f?/?\d+(\.\d+)?", s):
        n = float(s.lstrip("f/"))
        return f"f{n:.1f}" if n < 10 else f"f{n:g}"
    if key == "tv":
        if re.fullmatch(r"\d+(\.\d+)?", s):         # seconds: "2", "0.3"
            whole, _, frac = s.partition(".")
            return f'{whole}"{frac}' if frac else f'{whole}"'
        return s.lower() if s.lower() == "bulb" else s  # "1/125"
    if key == "exposure" and re.fullmatch(r"[+-]?\d+(\.\d+)?", s):
        sign = "-" if s.startswith("-") else "+"
        whole, _, frac = s.lstrip("+-").partition(".")
        frac = "0." + frac if frac else ""
        if not frac or frac in ("0.0",):
            return f"{sign}{whole}.0"
        return f"{sign}{whole}_{_FRACTION.get(frac, frac)}"
    if key == "wb":
        return WB.get(s, s)
    if key == "picture_style":
        u = re.match(r"User defined (\d)", s)
        return f"userdef{u[1]}" if u else s.lower().replace(" ", "")
    if key == "mode":
        return MODE.get(s, s)
    if key == "movie_servo_af":
        return s.lower()
    return s


def from_raw(key: str, raw: str, choices: list[str]) -> str:
    """The app's raw value -> the gphoto2 choice that maps to it (or raw itself if it's already a choice)."""
    if raw in choices:
        return raw
    for c in choices:
        if to_raw(key, c) == raw:
            return c
    raise ValueError(f"{raw!r} is not an option for {key} (camera offers {choices})")


def _canon_camera():
    """The Canon on USB, never another PTP device (phones show up too)."""
    import gphoto2 as gp
    found = [(n, a) for n, a in gp.Camera.autodetect() if "canon" in n.lower()]
    if not found:
        raise RuntimeError("no camera on USB (-105): only non-Canon devices found")
    name, addr = found[0]
    cam = gp.Camera()
    abilities = gp.CameraAbilitiesList()
    abilities.load()
    cam.set_abilities(abilities[abilities.lookup_model(name)])
    ports = gp.PortInfoList()
    ports.load()
    cam.set_port_info(ports[ports.lookup_path(addr)])
    return cam


class GPhotoTransport(Transport):
    name = "usb-gphoto"

    def __init__(self, camera_factory=None):
        """camera_factory() -> object with gphoto2.Camera's methods (tests pass a fake)."""
        if camera_factory is None:
            camera_factory = _canon_camera
        self.camera_factory = camera_factory
        self.cam = None
        self.worker: SdkThread | None = None
        self.names: dict[str, str] = {}  # app key -> config name this camera actually has
        self._rec_since: float | None = None
        self._pending_added: list[str] = []

    def _run(self, fn, *args, timeout: float = 30):
        if not self.worker:
            raise RuntimeError("not connected")
        return self.worker.call(fn, *args, timeout=timeout)

    # ---- lifecycle
    def connect(self) -> None:
        self.worker = SdkThread()
        try:
            self._run(self._connect)
        except Exception as e:
            self.worker.stop()
            self.worker = None
            msg = str(e)
            if "claim" in msg.lower() or "busy" in msg.lower() or "-53" in msg:
                msg += (" - macOS is holding the camera: quit Photos/Image Capture and run "
                        "`killall PTPCamera ptpcamerad`, then reconnect")
            elif "-105" in msg or "Unknown model" in msg or "not found" in msg.lower():
                msg = ("no camera on USB: connect it with a data cable, set the camera's USB connection app to "
                       "'Photo Import/Remote Control', and close EOS Utility")
            raise RuntimeError(msg) from e

    def _connect(self):
        self.cam = self.camera_factory()
        self.cam.init()
        cfg = self.cam.get_config()
        for key, candidates in CONFIG.items():
            for name in candidates:
                if _find(cfg, name) is not None:
                    self.names[key] = name
                    break

    def disconnect(self) -> None:
        if not self.worker:
            return
        try:
            self._run(lambda: self.cam.exit(), timeout=10)
        finally:
            self.worker.stop()
            self.worker = None

    # ---- info / settings
    def info(self) -> CameraInfo:
        def read():
            cfg = self.cam.get_config()
            model = _value(cfg, "cameramodel") or _value(cfg, "model") or "Canon camera"
            return model, _value(cfg, "deviceversion") or ""
        model, fw = self._run(read)
        return CameraInfo(model=f"{model} (USB)", firmware=str(fw))

    def capabilities(self) -> set[str]:
        return super().capabilities() - {"zoom", "zoom_move", "focus_at", "liveview_info"} | {"events", "discover"}

    def _setting(self, key: str) -> dict:
        w = self.cam.get_single_config(self.names[key])
        choices = [w.get_choice(i) for i in range(w.count_choices())] if hasattr(w, "count_choices") else []
        return {"value": to_raw(key, w.get_value()), "ability": [to_raw(key, c) for c in choices]}

    def get_setting(self, key: str) -> dict:
        if key not in self.names:
            raise NotSupported(f"{key} over USB (gphoto2)")
        return self._run(self._setting, key)

    def set_setting(self, key: str, value: str) -> None:
        if key not in self.names:
            raise NotSupported(f"{key} over USB (gphoto2)")

        def go():
            w = self.cam.get_single_config(self.names[key])
            choices = [w.get_choice(i) for i in range(w.count_choices())]
            w.set_value(from_raw(key, value, choices))
            self.cam.set_single_config(self.names[key], w)
        self._run(go)

    def _set_named(self, name: str, value):
        w = self.cam.get_single_config(name)
        w.set_value(value)
        self.cam.set_single_config(name, w)

    def focus_drive(self, step: int) -> None:
        """Nudge manual focus: -3..-1 nearer, 1..3 farther. Needs live view on."""
        if step not in (-3, -2, -1, 1, 2, 3):
            raise ValueError("focus step is -3..-1 (near) or 1..3 (far)")
        self._run(self._set_named, "manualfocusdrive", f"{'Far' if step > 0 else 'Near'} {abs(step)}")

    # ---- shooting
    def shoot(self, autofocus: bool = True) -> None:
        def go():
            import gphoto2 as gp
            path = self.cam.capture(gp.GP_CAPTURE_IMAGE)
            self._pending_added.append(f"{path.folder.rstrip('/')}/{path.name}")
        self._run(go, timeout=60)

    def half_press(self) -> None:
        def go():
            for v in ("Press Half", "Release Half"):
                self._set_named("eosremoterelease", v)
        self._run(go)

    def record(self, start: bool) -> None:
        def go():
            try:  # Canon EOS movie toggle; older libgphoto2 uses movierecordtarget
                self._set_named("movie", 1 if start else 0)
            except Exception:
                self._set_named("movierecordtarget", "Card" if start else "None")
        self._run(go)
        self._rec_since = time.time() if start else None

    # ---- live view / status
    def liveview_frame(self) -> bytes:
        return self._run(lambda: bytes(memoryview(self.cam.capture_preview().get_data_and_size())))

    def status(self) -> dict:
        def read():
            cfg = self.cam.get_config()
            bat = _value(cfg, "batterylevel")
            m = re.search(r"(\d+)\s*%", str(bat or ""))
            shots = _value(cfg, "availableshots")
            return {"battery": int(m[1]) if m else None, "temp": "normal",
                    "shots_left": int(shots) if str(shots or "").isdigit() else None,
                    "recording": int(time.time() - self._rec_since) if self._rec_since else None}
        return self._run(read)

    # ---- files
    def list_files(self) -> list[str]:
        def walk_all():
            out: list[str] = []

            def walk(folder: str):
                for name, _ in self.cam.folder_list_files(folder):
                    out.append(f"{folder.rstrip('/')}/{name}")
                for name, _ in self.cam.folder_list_folders(folder):
                    walk(f"{folder.rstrip('/')}/{name}")
            walk("/")
            return sorted(out)
        return self._run(walk_all, timeout=120)

    def download(self, remote: str, dest: Path, progress=None, cancel=None) -> Path:
        """gphoto2 reads a file in one call; cancel/progress apply before and after it."""
        if cancel and cancel():
            raise Cancelled(remote)
        dest.mkdir(parents=True, exist_ok=True)
        folder, name = remote.rsplit("/", 1)
        out = dest / name

        def go():
            import gphoto2 as gp
            f = self.cam.file_get(folder or "/", name, gp.GP_FILE_TYPE_NORMAL)
            f.save(str(out.with_name(out.name + ".part")))
        self._run(go, timeout=3600)
        part = out.with_name(out.name + ".part")
        if cancel and cancel():
            part.unlink(missing_ok=True)
            raise Cancelled(remote)
        part.replace(out)
        if progress:
            size = out.stat().st_size
            progress(size, size)
        return out

    def thumbnail(self, remote: str) -> bytes:
        folder, name = remote.rsplit("/", 1)

        def go():
            import gphoto2 as gp
            return bytes(memoryview(self.cam.file_get(folder or "/", name, gp.GP_FILE_TYPE_PREVIEW).get_data_and_size()))
        return self._run(go)

    # ---- events
    def events(self, timeout: float = 10) -> dict:
        def poll():
            import gphoto2 as gp
            added, changed = list(self._pending_added), False
            self._pending_added.clear()
            end = time.time() + min(timeout, 10)
            while time.time() < end and not added:
                kind, data = self.cam.wait_for_event(200)
                if kind == gp.GP_EVENT_FILE_ADDED:
                    added.append(f"{data.folder.rstrip('/')}/{data.name}")
                elif kind == gp.GP_EVENT_UNKNOWN and "Property" in str(data):
                    changed = True
                    break
                elif kind == gp.GP_EVENT_TIMEOUT and changed:
                    break
            return added, changed
        added, changed = self._run(poll, timeout=timeout + 10)
        out: dict = {}
        if added:
            out["added"] = added
        if changed:  # gphoto2 reports "a property changed" without which one: refresh the visible settings
            out["settings"] = {}
            for key in self.names:
                try:
                    out["settings"][key] = self.get_setting(key)
                except Exception:
                    pass
            out["status"] = self.status()
        return out

    def wait_new_files(self, timeout: float = 30) -> list[str]:
        return self.events(timeout).get("added", [])

    # ---- discovery
    def discover(self) -> list[dict]:
        """Every config entry the camera exposes over USB (name, label, value, number of choices)."""
        def scan():
            found = []

            def walk(w, path):
                for i in range(w.count_children()):
                    c = w.get_child(i)
                    p = f"{path}/{c.get_name()}"
                    if c.count_children():
                        walk(c, p)
                        continue
                    try:
                        value = c.get_value()
                    except Exception:
                        value = None
                    try:  # text/toggle/range widgets have no choices and raise "Bad parameters"
                        n = c.count_choices()
                    except Exception:
                        n = 0
                    try:
                        ro = bool(c.get_readonly())
                    except Exception:
                        ro = False
                    found.append({"id": p, "name": c.get_label(), "value": value, "options": n,
                                  "readonly": ro})
            walk(self.cam.get_config(), "")
            return found
        return self._run(scan, timeout=120)


def _find(cfg, name):
    try:
        return cfg.get_child_by_name(name)
    except Exception:
        return None


def _value(cfg, name):
    w = _find(cfg, name)
    try:
        return w.get_value() if w is not None else None
    except Exception:
        return None
