"""gphoto2 USB transport against a fake camera with gphoto2.Camera's interface."""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

gp = pytest.importorskip("gphoto2")
from studio_utility.core import Cancelled, NotSupported  # noqa: E402
from studio_utility.transports.gphoto import GPhotoTransport, from_raw, to_raw  # noqa: E402


class Widget:
    def __init__(self, name, value=None, choices=(), children=(), label=None, readonly=False):
        self.name, self.value, self.choices = name, value, list(choices)
        self.children, self.label, self.readonly = list(children), label or name, readonly

    def get_name(self): return self.name
    def get_label(self): return self.label
    def get_value(self): return self.value
    def set_value(self, v):
        if self.choices and v not in self.choices:
            raise ValueError(v)
        self.value = v
    def get_readonly(self): return self.readonly
    def count_choices(self): return len(self.choices)
    def get_choice(self, i): return self.choices[i]
    def count_children(self): return len(self.children)
    def get_child(self, i): return self.children[i]

    def get_child_by_name(self, name):
        for c in self.children:
            if c.name == name:
                return c
            try:
                return c.get_child_by_name(name)
            except KeyError:
                pass
        raise KeyError(name)


class FakeCamera:
    def __init__(self):
        W = Widget
        self.tree = W("main", children=[
            W("status", children=[W("cameramodel", "Canon EOS R50 V"), W("deviceversion", "1-1.2.0"),
                                  W("batterylevel", "50%"), W("availableshots", "2955")]),
            W("capturesettings", children=[
                W("iso", "800", ["Auto", "100", "800", "1600"]),
                W("shutterspeed", "1/125", ["1/50", "1/125", "0.3"]),
                W("aperture", "4", ["4", "5.6", "8"]),
                W("exposurecompensation", "0", ["-0.7", "0", "0.3"]),
                W("whitebalance", "Auto", ["Auto", "Daylight", "Shadow"]),
                W("picturestyle", "Auto", ["Auto", "Standard", "Fine Detail", "User defined 1"]),
                W("autoexposuremodedial", "Manual", ["P", "TV", "AV", "Manual"]),
                W("canonlog", "Off", ["Off", "Canon Log 3"], label="Canon Log"),  # what we hope to find
                W("zoomspeed", "8", [str(n) for n in range(1, 16)]),
                W("colortemperature", "5200", ["2500", "5200", "10000"]),
                W("whitebalanceadjusta", "0", [str(n) for n in range(-9, 10)]),
                W("whitebalanceadjustb", "0", [str(n) for n in range(-9, 10)]),
                W("movieservoaf", "On", ["Off", "On"])]),
            W("actions", children=[W("eosremoterelease", "None", ["None", "Press Half", "Release Half"]),
                                   W("manualfocusdrive", "None", ["Near 1", "Near 2", "Near 3", "None",
                                                                  "Far 1", "Far 2", "Far 3"]),
                                   W("movie", 0)])])
        self.threads, self.sets, self.events = set(), [], []
        self.files = {"/store_00020001/DCIM/100CANON": ["IMG_0001.JPG", "MVI_0002.MP4"]}

    def _t(self): self.threads.add(threading.get_ident())
    def init(self): self._t()
    def exit(self): self._t()
    def get_config(self): self._t(); return self.tree
    def get_single_config(self, name): self._t(); return self.tree.get_child_by_name(name)

    def set_single_config(self, name, w):
        self._t()
        self.sets.append((name, w.value))

    def capture(self, kind):
        self._t()
        return SimpleNamespace(folder="/store_00020001/DCIM/100CANON", name="IMG_0003.JPG")

    def capture_preview(self):
        return SimpleNamespace(get_data_and_size=lambda: b"\xff\xd8preview")

    def folder_list_folders(self, folder):
        f = folder.rstrip("/") or ""
        subs = {"": ["store_00020001"], "/store_00020001": ["DCIM"], "/store_00020001/DCIM": ["100CANON"]}
        return [(n, None) for n in subs.get(f, [])]

    def folder_list_files(self, folder):
        return [(n, None) for n in self.files.get(folder.rstrip("/"), [])]

    def file_get(self, folder, name, kind):
        data = b"\xff\xd8thumb" if kind == gp.GP_FILE_TYPE_PREVIEW else b"x" * 1000
        return SimpleNamespace(save=lambda p: open(p, "wb").write(data), get_data_and_size=lambda: data)

    def wait_for_event(self, ms):
        if self.events:
            return self.events.pop(0)
        return gp.GP_EVENT_TIMEOUT, None


@pytest.fixture
def cam():
    return FakeCamera()


@pytest.fixture
def t(cam):
    tr = GPhotoTransport(camera_factory=lambda: cam)
    tr.connect()
    yield tr
    tr.disconnect()


def test_info_and_single_camera_thread(t, cam):
    assert t.info().model == "Canon EOS R50 V (USB)"
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: t.get_setting("iso"), range(8)))
    assert len(cam.threads) == 1


def test_settings_translate_both_ways(t, cam):
    assert t.get_setting("tv") == {"value": "1/125", "ability": ["1/50", "1/125", '0"3']}
    assert t.get_setting("av")["value"] == "f4.0" and t.get_setting("mode")["value"] == "M"
    assert t.get_setting("exposure")["ability"] == ["-0_2/3", "+0.0", "+0_1/3"]
    assert t.get_setting("picture_style")["ability"][2:] == ["finedetail", "userdef1"]
    t.set_setting("tv", '0"3')
    t.set_setting("wb", "shade")
    assert ("shutterspeed", "0.3") in cam.sets and ("whitebalance", "Shadow") in cam.sets
    with pytest.raises(ValueError):
        t.set_setting("iso", "51200")
    with pytest.raises(NotSupported):
        t.get_setting("movie_size")


def test_focus_record_status_liveview(t, cam):
    t.half_press()
    t.record(True)
    t.record(False)
    assert [s for s in cam.sets if s[0] in ("eosremoterelease", "movie")] == [
        ("eosremoterelease", "Press Half"), ("eosremoterelease", "Release Half"), ("movie", 1), ("movie", 0)]
    assert t.status() == {"battery": 50, "temp": "normal", "shots_left": 2955, "recording": None}
    assert t.liveview_frame() == b"\xff\xd8preview"


def test_files_and_cancel(t, tmp_path):
    files = t.list_files()
    assert files == ["/store_00020001/DCIM/100CANON/IMG_0001.JPG", "/store_00020001/DCIM/100CANON/MVI_0002.MP4"]
    assert t.download(files[0], tmp_path).stat().st_size == 1000
    with pytest.raises(Cancelled):
        t.download(files[1], tmp_path, cancel=lambda: True)
    assert t.thumbnail(files[0]).startswith(b"\xff\xd8")


def test_events_new_files_and_property_changes(t, cam):
    t.shoot()
    assert t.events(1) == {"added": ["/store_00020001/DCIM/100CANON/IMG_0003.JPG"]}
    cam.events.append((gp.GP_EVENT_FILE_ADDED, SimpleNamespace(folder="/store_00020001/DCIM/100CANON/",
                                                                name="MVI_0004.MP4")))
    assert t.events(1)["added"] == ["/store_00020001/DCIM/100CANON/MVI_0004.MP4"]
    cam.events.append((gp.GP_EVENT_UNKNOWN, "PTP Property d102 changed"))
    ev = t.events(1)
    assert ev["settings"]["iso"]["value"] == "800" and ev["status"]["battery"] == 50


def test_discover_finds_every_config_including_canon_log(t):
    found = {p["id"]: p for p in t.discover()}
    assert found["/capturesettings/canonlog"]["name"] == "Canon Log"
    assert found["/capturesettings/canonlog"]["options"] == 2


@pytest.mark.parametrize("key,gp_value,raw", [("tv", "2", '2"'), ("exposure", "1.7", "+1_2/3"), ("iso", "Auto", "auto")])
def test_value_round_trip(key, gp_value, raw):
    assert to_raw(key, gp_value) == raw
    assert from_raw(key, raw, [gp_value]) == gp_value


def test_busy_device_message_explains_ptpcamera():
    def busy():
        raise RuntimeError("[-53] Could not claim the USB device")
    t = GPhotoTransport(camera_factory=busy)
    with pytest.raises(RuntimeError, match="killall PTPCamera"):
        t.connect()


def test_usb_only_controls(t, cam):
    assert t.get_setting("movie_servo_af") == {"value": "on", "ability": ["off", "on"]}
    assert t.get_setting("wb_shift_ab")["value"] == "0" and t.get_setting("zoom_speed")["value"] == "8"
    t.set_setting("movie_servo_af", "off")
    t.set_setting("wb_shift_gm", "-4")
    t.set_setting("color_temp", "2500")
    t.focus_drive(-2)
    t.focus_drive(3)
    assert [("movieservoaf", "Off"), ("whitebalanceadjustb", "-4"), ("colortemperature", "2500"),
            ("manualfocusdrive", "Near 2"), ("manualfocusdrive", "Far 3")] == cam.sets[-5:]
    with pytest.raises(NotSupported):
        t.get_setting("color_filter")  # libgphoto2 doesn't expose it; Canon's EDSDK does
