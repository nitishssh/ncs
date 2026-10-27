"""EDSDK transport against a fake SDK (same methods as edsdk_native.Sdk). Verifies the app logic - code
mapping, threading, live view retries, files, events, discovery, hub fallback - before the real library
is available. The ctypes layer itself is only verified with Canon's SDK on the real camera."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from studio_utility import edsdk_codes as codes
from studio_utility.core import Cancelled, NotSupported
from studio_utility.edsdk_native import EdsError
from studio_utility.transports.edsdk import EDSDKTransport

CAM, VOL, DCIM, FOLDER, IMG, MOV = 1, 10, 11, 12, 13, 14


class FakeSdk:
    def __init__(self):
        P = codes.PROP
        self.props = {P["product_name"]: "Canon EOS R50 V", P["firmware"]: "1.2.0", P["battery"]: 0xFFFFFFFF,
                      P["shots_left"]: 2955, P["iso"]: 0x60, P["tv"]: 0x70, P["av"]: 0x28, P["mode"]: 3,
                      P["exposure"]: 0x00, P["wb"]: 1, P["picture_style"]: 0x87, P["evf_output"]: 1,
                      P["record"]: 0, 0x01000123: 7,  # an unnamed property for discover()
                      P["zoom_speed"]: 8, P["color_temp"]: 5200, P["color_filter"]: 6,
                      P["wb_shift"]: b"\x00" * 8, P["clip_prefix"]: "A_", P["clip_name"]: "CANON"}
        self.options = {P["iso"]: [0x00, 0x48, 0x60, 0x68], P["tv"]: [0x65, 0x70, 0x78], P["wb"]: [0, 1, 8],
                        P["zoom_speed"]: list(range(1, 16)), P["color_temp"]: list(range(2500, 10001, 100)),
                        P["color_filter"]: [0, 1, 6, 14]}
        self.tree = {CAM: [VOL], VOL: [DCIM], DCIM: [FOLDER], FOLDER: [IMG, MOV]}
        self.files = {VOL: ("card1", 0, True), DCIM: ("DCIM", 0, True), FOLDER: ("100CANON", 0, True),
                      IMG: ("IMG_0001.JPG", 3_500_000, False), MOV: ("MVI_0002.MP4", 1_200_000, False)}
        self.commands, self.sets, self.evf_calls, self.threads = [], [], 0, set()
        self.pending = []  # events delivered on the next pump()
        self.handlers = {}
        self.released = []

    def _t(self):
        self.threads.add(threading.get_ident())

    def initialize(self):
        self._t()
        self.running = getattr(self, "running", 0) + 1
        if self.running > 1:  # like the real SDK: a second start without a stop fails
            raise EdsError("initialize", 0x02)

    def terminate(self):
        self._t()
        self.running = getattr(self, "running", 1) - 1
    def cameras(self): self._t(); return [(CAM, "Canon EOS R50 V")]
    def open(self, cam): self._t()
    def close(self, cam): self._t()
    def release(self, ref): self.released.append(ref)

    def pump(self):
        self._t()
        while self.pending:
            kind, *args = self.pending.pop(0)
            self.handlers[kind](*args)

    def get_property(self, ref, pid, param=0):
        self._t()
        if pid not in self.props:
            raise EdsError("property", codes.ERR_NOT_SUPPORTED)
        return self.props[pid]

    def property_options(self, ref, pid):
        return self.options.get(pid, [])

    def set_property(self, ref, pid, value, param=0):
        self._t()
        self.sets.append((pid, value))
        self.props[pid] = value

    def set_property_data(self, ref, pid, data, param=0):
        self._t()
        self.sets.append((pid, data))
        self.props[pid] = data.decode() if pid in (codes.PROP["clip_prefix"], codes.PROP["clip_name"]) else data

    def command(self, cam, cmd, param=0):
        self._t()
        self.commands.append((cmd, param))

    def download_evf(self, cam):
        self._t()
        self.evf_calls += 1
        if self.evf_calls <= 2:
            raise EdsError("live view", codes.ERR_OBJECT_NOTREADY)
        return b"\xff\xd8frame\xff\xd9"

    def children(self, ref): return list(self.tree.get(ref, []))

    def item_info(self, item):
        name, size, folder = self.files[item]
        return {"name": name, "size": size, "folder": folder}

    def download(self, item, path, size, progress=None, cancel=None, chunk=1 << 20):
        with open(path, "wb") as f:
            done = 0
            while done < size:
                if cancel and cancel():
                    return False
                n = min(chunk, size - done)
                f.write(b"\0" * n)
                done += n
                if progress:
                    progress(done, size)
        return True

    def thumbnail(self, item): return b"\xff\xd8thumb"
    def on_property(self, cam, fn): self.handlers["prop"] = fn
    def on_object(self, cam, fn): self.handlers["obj"] = fn
    def on_state(self, cam, fn): self.handlers["state"] = fn


@pytest.fixture
def fake():
    return FakeSdk()


@pytest.fixture
def t(fake):
    tr = EDSDKTransport(sdk_factory=lambda: fake)
    tr.connect()
    yield tr
    tr.disconnect()


def test_connect_saves_to_card_and_reports_model(t, fake):
    assert t.info().model == "Canon EOS R50 V (USB)"
    assert (codes.PROP["save_to"], codes.SAVE_TO_CAMERA) in fake.sets


def test_every_sdk_call_runs_on_one_thread(t, fake):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(4) as pool:  # the hub calls from several threads
        list(pool.map(lambda _: t.get_setting("iso"), range(8)))
    assert len(fake.threads) == 1 and threading.get_ident() not in fake.threads


def test_settings_use_readable_values(t, fake):
    iso = t.get_setting("iso")
    assert iso == {"value": "800", "ability": ["auto", "100", "800", "1600"]}  # 0x60 is ISO 800
    assert t.get_setting("tv")["value"] == "1/125" and t.get_setting("wb")["value"] == "daylight"
    t.set_setting("tv", "1/50")
    assert fake.props[codes.PROP["tv"]] == 0x65
    with pytest.raises(NotSupported):
        t.get_setting("movie_size")  # not exposed by EDSDK; Wi-Fi/camera menu


def test_shoot_focus_and_record(t, fake):
    t.shoot()
    t.half_press()
    assert fake.commands == [(codes.CMD_PRESS_SHUTTER, 3), (codes.CMD_PRESS_SHUTTER, 0),
                             (codes.CMD_PRESS_SHUTTER, 1), (codes.CMD_PRESS_SHUTTER, 0)]
    t.record(True)
    assert fake.props[codes.PROP["record"]] == codes.RECORD_START and t.status()["recording"] == 0
    t.record(False)
    assert fake.props[codes.PROP["record"]] == codes.RECORD_STOP


def test_record_when_busy_names_every_likely_cause(t, fake):
    def busy(ref, pid, value, param=0):
        raise EdsError("set property 0x510", codes.ERR_DEVICE_BUSY)
    fake.set_property = busy
    with pytest.raises(RuntimeError, match="already recording.*menu.*movie mode"):
        t.record(True)


def test_live_view_turns_on_pc_output_and_waits_for_first_frame(t, fake):
    assert t.liveview_frame() == b"\xff\xd8frame\xff\xd9"
    assert fake.props[codes.PROP["evf_output"]] & codes.EVF_OUTPUT_PC  # kept the camera screen bit too
    assert fake.evf_calls == 3  # two "not ready" retries


def test_status_battery_on_usb_power_is_unknown(t):
    s = t.status()
    assert s["battery"] is None and s["shots_left"] == 2955


def test_files_list_download_and_cancel(t, fake, tmp_path):
    files = t.list_files()
    # the card itself is a volume, not a folder item: paths start at DCIM (the R50 V has one card slot)
    assert files == ["edsdk:/DCIM/100CANON/IMG_0001.JPG", "edsdk:/DCIM/100CANON/MVI_0002.MP4"]
    ticks = []
    out = t.download(files[0], tmp_path, progress=lambda d, n: ticks.append(d))
    assert out.stat().st_size == 3_500_000 and len(ticks) == 4 and not list(tmp_path.glob("*.part"))
    with pytest.raises(Cancelled):
        t.download(files[1], tmp_path / "c", cancel=lambda: True)
    assert list((tmp_path / "c").iterdir()) == []
    assert t.thumbnail(files[0]).startswith(b"\xff\xd8")


def test_events_from_camera_callbacks(t, fake):
    t.events(0.1)
    fake.pending += [("prop", codes.PROPERTY_EVENT_CHANGED, codes.PROP["iso"], 0),
                     ("obj", codes.OBJECT_EVENT_DIR_ITEM_REQUEST_TRANSFER, IMG)]
    ev = t.events(2)  # the SDK thread pumps, the handlers fire, events() wakes up
    assert ev["settings"]["iso"]["value"] == "800"
    assert ev["added"] == ["edsdk:/new/IMG_0001.JPG"]
    fake.pending.append(("state", codes.STATE_EVENT_SHUTDOWN, 0))
    assert "camera_lost" in t.events(2)


def test_discover_lists_answering_properties(t):
    found = {p["id"]: p for p in t.discover()}
    assert found["0x00000402"]["name"] == "iso" and found["0x00000402"]["options"] == 4
    assert "0x01000123" in found  # unknown IDs are reported too - that's how Canon Log would show up


def test_missing_sdk_says_what_to_do(tmp_path, monkeypatch):
    monkeypatch.delenv("STUDIO_UTILITY_EDSDK", raising=False)
    from studio_utility import edsdk_native
    monkeypatch.setattr(edsdk_native, "ROOT", tmp_path)
    monkeypatch.setattr(edsdk_native.Path, "home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    t = EDSDKTransport()
    with pytest.raises(FileNotFoundError, match="EDSDK_TODO"):
        t.connect()


def test_env_may_point_at_the_framework_folder(monkeypatch, tmp_path):
    from studio_utility import edsdk_native
    lib = tmp_path / "EDSDK.framework" / "EDSDK"
    lib.parent.mkdir()
    lib.touch()
    monkeypatch.setenv("STUDIO_UTILITY_EDSDK", str(lib.parent))
    assert edsdk_native.find_library() == lib


def test_hub_prefers_usb_and_falls_back(monkeypatch, tmp_path):
    from studio_utility.hub import server
    monkeypatch.setattr(server, "CONFIG", tmp_path / "hub.json")
    hub = server.Hub("mock", prefer_usb=True)
    assert [k for k, _ in hub.candidates] == ["usb-edsdk", "mock"]


def test_codes_round_trip():
    for key in ("iso", "tv", "av", "exposure", "wb", "picture_style"):
        for code, value in codes.TABLES[key].items():
            assert codes.to_value(key, codes.to_code(key, value)) == value
    assert codes.to_value("iso", 0x12345) == "0x12345" and codes.to_code("iso", "0x12345") == 0x12345


def test_usb_only_controls(t, fake):
    assert t.get_setting("zoom_speed") == {"value": "8", "ability": [str(n) for n in range(1, 16)]}
    assert t.get_setting("color_temp")["value"] == "5200"
    assert t.get_setting("color_filter") == {"value": "story_teal_orange",
                                             "ability": ["off", "pale_teal_orange", "story_teal_orange", "accent_red"]}
    t.set_setting("zoom_speed", "15")
    t.set_setting("color_temp", "3200")
    t.set_setting("color_filter", "accent_red")
    P = codes.PROP
    assert fake.props[P["zoom_speed"]] == 15 and fake.props[P["color_temp"]] == 3200 and fake.props[P["color_filter"]] == 14


def test_wb_shift_writes_both_axes(t, fake):
    import struct
    assert t.get_setting("wb_shift_ab") == {"value": "0", "ability": [str(n) for n in range(-9, 10)]}
    t.set_setting("wb_shift_ab", "-3")
    t.set_setting("wb_shift_gm", "2")
    assert struct.unpack("<2i", fake.props[codes.PROP["wb_shift"]]) == (-3, 2)
    assert t.get_setting("wb_shift_ab")["value"] == "-3"
    with pytest.raises(ValueError):
        t.set_setting("wb_shift_gm", "10")


def test_clip_names_are_fixed_length_text(t, fake):
    assert t.get_setting("clip_name") == {"value": "CANON", "ability": [], "max_length": 5}
    t.set_setting("clip_name", "vlog1")
    assert (codes.PROP["clip_name"], b"VLOG1") in fake.sets  # no trailing NUL: Canon's sample sends none
    with pytest.raises(ValueError):
        t.set_setting("clip_prefix", "ABC")


def test_focus_drive_steps(t, fake):
    for step in (-3, -1, 1, 3):
        t.focus_drive(step)
    assert fake.commands == [(codes.CMD_DRIVE_LENS_EVF, p) for p in (0x3, 0x1, 0x8001, 0x8003)]
    assert "focus_drive" in t.capabilities()
    with pytest.raises(ValueError):
        t.focus_drive(0)


def test_mode_locked_settings_show_no_bogus_options(t, fake):
    """In P mode the R50 V answers shutter/iris options with one undocumented placeholder (seen: 0x1500, 0xF30)."""
    fake.options[codes.PROP["tv"]] = [0x1500]
    fake.options[codes.PROP["av"]] = [0xF30]
    assert t.get_setting("tv") == {"value": "1/125", "ability": []}
    assert t.get_setting("av")["ability"] == []


def test_single_option_means_locked(t, fake):
    """ISO fixed to auto in P mode: options [auto], value is the live ISO, and setting it answers 'busy'."""
    fake.options[codes.PROP["iso"]] = [0x00]
    fake.props[codes.PROP["iso"]] = 0x60
    assert t.get_setting("iso") == {"value": "800", "ability": []}


def test_lost_session_is_reported_not_retried(t, fake):
    """Turning the R50 V's mode dial (C3 -> M) closes the USB session: say so, so the hub reconnects."""
    from studio_utility.core import CameraLost

    def gone(*a, **k):
        raise EdsError("property", 0x00002003)  # EDS_ERR_SESSION_NOT_OPEN
    fake.get_property = gone
    with pytest.raises(CameraLost):
        t.get_setting("iso")
    assert "camera_lost" in t.events(1)


def test_failed_connect_and_dead_session_still_stop_the_sdk(fake):
    """A camera stuck after a mode-dial change: open fails, then later close fails. Each time the SDK must be
    stopped, or every reconnect after that fails with 'initialize: internal error'."""
    opens = []

    def stuck_open(cam):
        opens.append(cam)
        if len(opens) == 1:
            raise EdsError("open session", 0x2003)
    fake.open = stuck_open
    t = EDSDKTransport(sdk_factory=lambda: fake)
    with pytest.raises(Exception):
        t.connect()
    assert fake.running == 0
    t.connect()  # the retry works

    def dead(cam):
        raise EdsError("close", 0xC1)
    fake.close = dead
    t.disconnect()
    assert fake.running == 0
    EDSDKTransport(sdk_factory=lambda: fake).connect()


def test_live_view_restarts_after_camera_turned_it_off(t, fake):
    """After a mode-dial change the camera drops PC output: frames fail with 0xA104 until it's switched back on."""
    assert t.liveview_frame().startswith(b"\xff\xd8")
    fake.props[codes.PROP["evf_output"]] = 1  # camera screen only, PC bit cleared by the camera
    fake.props[codes.PROP["evf_mode"]] = 0
    real = fake.download_evf

    def evf(cam):
        if not fake.props[codes.PROP["evf_output"]] & codes.EVF_OUTPUT_PC:
            raise EdsError("live view frame", codes.ERR_CANNOT_MAKE_OBJECT)
        return real(cam)
    fake.download_evf = evf
    assert t.liveview_frame().startswith(b"\xff\xd8")
    assert fake.props[codes.PROP["evf_output"]] & codes.EVF_OUTPUT_PC and fake.props[codes.PROP["evf_mode"]] == 1


def test_live_view_rearms_when_camera_still_reports_pc_output(t, fake):
    """Seen on the R50 V after C3 -> M: output still reads screen+PC (3) but every frame fails with 0xA104.
    Only clearing and setting the PC bit again restarts it."""
    assert t.liveview_frame()
    P = codes.PROP["evf_output"]
    fake.props[P] = 3
    armed = {"ok": False}
    real_set, real_evf = fake.set_property, fake.download_evf

    def set_prop(ref, pid, value, param=0):
        if pid == P and not value & codes.EVF_OUTPUT_PC:
            armed["ok"] = True  # the camera saw live view stop; the next start is real
        real_set(ref, pid, value, param)

    def evf(cam):
        if not armed["ok"]:
            raise EdsError("live view frame", codes.ERR_CANNOT_MAKE_OBJECT)
        return real_evf(cam)
    fake.set_property, fake.download_evf = set_prop, evf
    t.EVF_STALL, t.EVF_REARM_GAP = 0.2, 0.01
    assert t.liveview_frame().startswith(b"\xff\xd8")
    assert fake.props[P] & codes.EVF_OUTPUT_PC


def test_live_view_is_not_rearmed_on_every_request(t, fake):
    """The regression found on the Mac: re-arming whenever a frame wasn't ready restarted live view every second,
    so it never came up. While it's starting, the app just waits."""
    assert t.liveview_frame()
    P = codes.PROP["evf_output"]
    toggles = []
    real_set, real_evf = fake.set_property, fake.download_evf
    started = time.monotonic()

    def set_prop(ref, pid, value, param=0):
        if pid == P:
            toggles.append(value)
        real_set(ref, pid, value, param)

    def evf(cam):  # the camera needs 0.5 s before the first image
        if time.monotonic() - started < 0.5:
            raise EdsError("live view frame", codes.ERR_CANNOT_MAKE_OBJECT)
        return real_evf(cam)
    fake.set_property, fake.download_evf = set_prop, evf
    t._last_frame = None
    t._evf_since = time.monotonic()
    assert t.liveview_frame().startswith(b"\xff\xd8")
    assert toggles == []


def test_session_left_open_by_a_timed_out_connect_is_closed_first(fake, monkeypatch):
    """Seen on the Windows hub: a connect gave up after its timeout, finished in the background and kept the
    camera open; every later connect failed with 'communication port in use' until the hub restarted."""
    from studio_utility.transports import edsdk as mod
    t1 = EDSDKTransport(sdk_factory=lambda: fake)
    t1.connect()
    t1.worker = None  # abandoned without disconnect, like the timed-out connect
    closed = []
    real_close = fake.close
    fake.close = lambda cam: (closed.append(cam), real_close(cam))
    fake.running = 0  # the leftover's SDK start is ours to stop; keep the fake's start/stop count simple
    t2 = EDSDKTransport(sdk_factory=lambda: fake)
    t2.connect()
    assert closed == [CAM] and mod._open_session["s"][1] == CAM
    t2.disconnect()
    assert "s" not in mod._open_session


def test_mac_keeps_one_sdk_alive_so_reconnect_finds_the_camera(monkeypatch):
    """macOS: terminate + initialize in one process loses a camera that stayed plugged in, so the SDK starts once
    and disconnect only closes the session; connect -> disconnect -> connect must not restart it."""
    from studio_utility.transports import edsdk as mod
    monkeypatch.setattr(mod, "_resident", {})
    fake = FakeSdk()
    for _ in range(3):
        tr = EDSDKTransport(sdk_factory=lambda: fake)
        tr.keep_sdk_alive = True
        tr.connect()
        tr.disconnect()
    assert fake.running == 1  # initialized once, never terminated (a second initialize would have raised)


def test_quitting_shuts_the_resident_sdk_down(monkeypatch):
    """Left running, the dead process kept the camera: the next launch found 'no camera on USB'."""
    from studio_utility.transports import edsdk as mod
    monkeypatch.setattr(mod, "_resident", {})
    fake = FakeSdk()
    tr = EDSDKTransport(sdk_factory=lambda: fake)
    tr.keep_sdk_alive = True
    tr.connect()
    tr.disconnect()
    assert fake.running == 1
    mod.release_resident()
    assert fake.running == 0 and mod._resident == {}
    mod.release_resident()  # quitting twice (closeEvent, then atexit) is harmless


def test_photo_in_movie_mode_says_so(t, fake):
    """Seen on the R50 V: Shoot on the movie side failed with a bare 'EDSDK error 0x00008D0C'."""
    def refuse(cam, cmd, param=0):
        raise EdsError("command 0x4", 0x8D0C)
    fake.command = refuse
    with pytest.raises(EdsError, match="photo/movie switch to photo"):
        t.shoot()


def test_aperture_placeholder_reads_auto_not_f1(t, fake):
    """R50 V in P/movie auto: Av came back as 0x08 (f/1.0) on an F4 lens with no real choices."""
    from studio_utility import edsdk_codes as codes
    get = fake.get_property

    def prop(cam, pid):
        if pid == codes.PROP["av"]:
            return 0x08
        if pid == codes.PROP["lens_name"]:
            return "RF-S14-30mm F4-6.3 IS STM PZ"
        return get(cam, pid)
    fake.get_property = prop
    fake.property_options = lambda cam, pid: [0xF30]
    assert t.get_setting("av") == {"value": "auto", "ability": []}


def test_keeps_the_camera_awake_while_connected(t, fake, monkeypatch):
    from studio_utility import edsdk_codes as codes
    sent = []
    fake.command = lambda cam, cmd, param=0: sent.append(cmd)
    t.status()
    t.status()  # within a minute: no second command
    assert sent.count(codes.CMD_EXTEND_SHUTDOWN) == 1
