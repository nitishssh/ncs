"""Hub + hub transport with the mock camera: pairing, shared state across devices, the camera limit."""
from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from studio_utility.hub import server  # noqa: E402
from studio_utility.transports.hub import HubTransport, parse_link  # noqa: E402


@pytest.fixture
def hub(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "CONFIG", tmp_path / "hub.json")
    monkeypatch.setattr(server, "CACHE", tmp_path / "cache")
    h = server.Hub("mock")
    loop = asyncio.new_event_loop()
    runner = web.AppRunner(h.app())
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", 0)
    loop.run_until_complete(site.start())
    port = site._server.sockets[0].getsockname()[1]
    threading.Thread(target=loop.run_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(50):  # wait for the mock camera to connect
        if httpx.get(base + "/api/state", headers={"X-Studio-Token": h.token}).json()["connection"] == "connected":
            break
        time.sleep(0.05)
    yield h, base
    asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)


def client(h, base, device):
    return httpx.Client(base_url=base, headers={"X-Studio-Token": h.token, "X-Studio-Device": device}, timeout=10)


def test_requires_pairing_token(hub):
    h, base = hub
    assert httpx.get(base + "/api/state").status_code == 401
    assert httpx.get(base + "/api/state", headers={"X-Studio-Token": "wrong"}).status_code == 401
    page = httpx.get(base + "/")
    assert page.status_code == 401 and "Pair this device" in page.text


def test_qr_link_sets_cookie(hub):
    h, base = hub
    r = httpx.get(base + f"/?token={h.token}")
    assert r.status_code == 200 and "studio_token" in r.cookies
    device_token = r.cookies["studio_token"]
    assert device_token != h.token
    assert httpx.get(base + "/api/state", cookies={"studio_token": device_token}).status_code == 200


def test_device_roles_and_revocation(hub):
    h, base = hub
    device_id, token = h.issue_device("Client monitor", "viewer")
    viewer = httpx.Client(base_url=base, headers={"X-Studio-Token": token}, timeout=10)

    assert viewer.get("/api/state").status_code == 200
    denied = viewer.post("/api/shoot")
    assert denied.status_code == 403 and "view-only" in denied.text

    admin = client(h, base, "Admin")
    devices = admin.get("/api/devices").json()
    assert any(d["id"] == device_id and d["role"] == "viewer" and "token" not in d for d in devices)
    admin.put(f"/api/devices/{device_id}/role", json={"role": "operator"}).raise_for_status()
    viewer.post("/api/shoot").raise_for_status()
    admin.delete(f"/api/devices/{device_id}").raise_for_status()
    assert viewer.get("/api/state").status_code == 401


def test_device_cannot_manage_other_devices(hub):
    h, base = hub
    _device_id, token = h.issue_device("Operator", "operator")
    operator = httpx.Client(base_url=base, headers={"X-Studio-Token": token}, timeout=10)
    assert operator.get("/api/devices").status_code == 403


def test_recording_on_one_device_reaches_others(hub):
    h, base = hub
    phone, mac = client(h, base, "iPhone"), client(h, base, "Mac")
    since = mac.get("/api/state").json()["seq"]
    phone.post("/api/record", json={"start": True}).raise_for_status()
    evs = mac.get("/api/events", params={"since": since, "timeout": 5}).json()["events"]
    rec = [e for e in evs if e["type"] == "record"][0]
    assert rec["by"] == "iPhone" and rec["recording"]["by"] == "iPhone"
    assert mac.get("/api/state").json()["recording"] is not None


def test_events_long_poll_waits_then_returns(hub):
    h, base = hub
    c = client(h, base, "Mac")
    seq = c.get("/api/state").json()["seq"]
    t0 = time.time()
    assert c.get("/api/events", params={"since": seq, "timeout": 0.6}).json()["events"] == []
    assert time.time() - t0 >= 0.5


def test_setting_change_is_broadcast(hub):
    h, base = hub
    c = client(h, base, "iPhone")
    seq = c.get("/api/state").json()["seq"]
    assert c.put("/api/settings/iso", json={"value": "1600"}).json()["value"] == "1600"
    evs = c.get("/api/events", params={"since": seq, "timeout": 5}).json()["events"]
    assert any(e.get("settings", {}).get("iso", {}).get("value") == "1600" for e in evs)


def test_live_view_and_files(hub):
    h, base = hub
    c = client(h, base, "iPhone")
    assert c.get("/api/live.jpg").content[:2] == b"\xff\xd8"
    files = c.get("/api/files").json()
    assert c.get("/api/thumb", params={"path": files[0]}).content[:2] == b"\xff\xd8"
    assert c.get("/api/file", params={"path": files[0]}).status_code == 200


def test_camera_calls_never_exceed_two(hub, monkeypatch):
    h, base = hub
    active, peak, lock = [0], [0], threading.Lock()
    real = h.t.get_setting

    def slow(key):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.15)
        with lock:
            active[0] -= 1
        return real(key)
    monkeypatch.setattr(h.t, "get_setting", slow)
    threads = [threading.Thread(target=lambda: client(h, base, "x").get("/api/settings/iso")) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 2  # 8 devices asking at once still means at most 2 camera requests


def test_hub_transport_in_desktop_app_terms(hub, tmp_path):
    h, base = hub
    t = HubTransport(f"{base}/?token={h.token}", device="Mac")
    t.connect()
    assert t.info().model.endswith("via hub") and "events" in t.capabilities()
    t.events(0.1)  # drain the backlog
    client(h, base, "iPhone").post("/api/record", json={"start": True})
    ev = t.events(5)
    assert ev["recording"] and ev["by"] == "iPhone"
    path = t.download(t.list_files()[0], tmp_path)
    assert path.stat().st_size > 0
    t.disconnect()


def test_parse_link():
    assert parse_link("http://10.63.196.242:8765/?token=abc") == ("http://10.63.196.242:8765", "abc")
    assert parse_link("10.11.0.2:8765/?token=xyz") == ("http://10.11.0.2:8765", "xyz")


def test_live_view_graded_preview(hub):
    h, base = hub
    c = client(h, base, "iPhone")
    flat = c.get("/api/live.jpg").content
    graded = c.get("/api/live.jpg", params={"lut": "clog3"}).content
    assert graded[:2] == b"\xff\xd8" and graded != flat  # a different (graded) JPEG, same frame


def test_android_app_pairing(hub, monkeypatch):
    """The app trades the QR's admin secret for its own key, then reaches the hub with only that key."""
    h, base = hub
    monkeypatch.setattr(server, "lan_addresses", lambda: ["192.168.0.100", "100.97.69.73"])
    assert httpx.post(base + "/api/pair", json={}).status_code == 401  # no secret, no key
    r = httpx.post(base + "/api/pair", json={"name": "Android app"}, headers={"X-Studio-Token": h.token})
    key = r.json()["device_token"]
    assert key != h.token and r.json()["hosts"][0].startswith("192.168.0.100:")
    hello = httpx.get(base + "/api/hello", headers={"X-Studio-Token": key}).json()
    assert hello["app"] == "studio-utility-hub" and hello["camera"] == "connected"
    assert httpx.get(base + "/api/state", cookies={"studio_token": key}).status_code == 200  # the WebView's cookie
    names = [d["name"] for d in httpx.get(base + "/api/devices", headers={"X-Studio-Token": h.token}).json()]
    assert "Android app" in names
    # a paired device can't mint more keys
    assert httpx.post(base + "/api/pair", json={}, headers={"X-Studio-Token": key}).status_code == 403


def test_one_pairing_code_lists_every_address(hub, monkeypatch):
    h, base = hub
    monkeypatch.setattr(server, "lan_addresses", lambda: ["192.168.0.100", "10.63.196.242", "100.97.69.73"])
    link = h.pairing_link(8765)
    assert link.startswith("http://192.168.0.100:8765/?token=") and link.endswith("&hosts=192.168.0.100,10.63.196.242,100.97.69.73")
    assert parse_link(link) == ("http://192.168.0.100:8765", h.token)  # desktop app still reads it


def test_device_names_are_readable():
    assert server.device_name("Mozilla/5.0 (Linux; Android 14) ... StudioUtilityAndroid/0.2") == "Android app"
    assert server.device_name("Mozilla/5.0 (Linux; Android 14; Pixel)") == "Android phone"
    assert server.device_name("Mozilla/5.0 (iPhone; CPU iPhone OS 18_0)") == "iPhone"


def test_hub_reconnects_when_camera_resets_its_link(hub):
    """A lost session (mode dial moved) or three failures in a row: the hub drops the camera and reconnects."""
    from studio_utility.core import CameraLost
    h, base = hub
    c = client(h, base, "test")
    old = h.t

    def lost(*a):
        raise CameraLost("session not open")
    old.get_setting = lost
    r = c.get("/api/settings/iso")
    assert r.status_code == 503 and "reconnecting" in r.text
    for _ in range(100):  # connect_loop brings up a fresh transport
        if h.t is not None and h.t is not old:
            break
        time.sleep(0.05)
    assert h.t is not None and h.t is not old
    assert c.get("/api/settings/iso").status_code == 200


def test_hub_survives_one_odd_failure(hub):
    h, base = hub
    c = client(h, base, "test")
    t, calls = h.t, []
    real = t.get_setting

    def flaky(key):
        calls.append(key)
        if len(calls) == 1:
            raise RuntimeError("device busy")
        return real(key)
    t.get_setting = flaky
    assert c.get("/api/settings/iso").status_code == 502
    assert c.get("/api/settings/iso").status_code == 200 and h.t is t  # no reconnect for a single hiccup


def test_live_view_failures_dont_drop_the_camera(hub):
    """A camera that won't give live frames in some mode must not be treated as gone (settings still work)."""
    h, base = hub
    c = client(h, base, "test")
    t = h.t

    def no_frames():
        raise RuntimeError("live view: object not ready")
    t.liveview_frame = no_frames
    for _ in range(4):
        r = c.get("/api/live.jpg")
        assert r.status_code == 503
    assert "object not ready" in r.text
    assert h.t is t and c.get("/api/settings/iso").status_code == 200


def test_restart_for_stale_usb_only_under_launchd_on_mac(monkeypatch):
    from studio_utility.hub import server
    monkeypatch.setattr(server.sys, "platform", "darwin")
    monkeypatch.setattr(server, "canon_on_usb", lambda: True)
    monkeypatch.setenv("XPC_SERVICE_NAME", "com.studio-utility.hub")
    err = "usb-edsdk: no camera on USB: set the camera's USB connection; wifi: timed out"
    assert server.restart_for_stale_usb(True, err)
    assert not server.restart_for_stale_usb(False, err)  # never had the camera: a fresh process wouldn't help
    monkeypatch.setattr(server, "canon_on_usb", lambda: False)
    assert not server.restart_for_stale_usb(True, err)  # really unplugged: keep waiting
    monkeypatch.setattr(server, "canon_on_usb", lambda: True)
    monkeypatch.delenv("XPC_SERVICE_NAME")
    assert not server.restart_for_stale_usb(True, err)  # run by hand: nobody would restart it


def _wav_bytes(seconds=0.5):
    import io

    import numpy as np
    import soundfile as sf
    buf = io.BytesIO()
    sf.write(buf, np.zeros((int(48000 * seconds), 1), np.float32), 48000, format="WAV", subtype="PCM_16")
    return buf.getvalue()


def test_phone_mic_upload_is_named_for_sync(hub, tmp_path, monkeypatch):
    pytest.importorskip("soundfile")
    from studio_utility import sync
    h, base = hub
    monkeypatch.setattr(server, "PHONE_AUDIO", tmp_path / "phone")
    c = client(h, base, "Pixel 8")
    started = time.mktime((2026, 10, 26, 14, 30, 5, 0, 0, -1)) * 1000
    r = c.post("/api/audio", content=_wav_bytes(),
               headers={"Content-Type": "audio/wav", "X-Audio-Started": str(int(started))})
    assert r.status_code == 200, r.text
    name = r.json()["name"]
    assert name == "2026-10-26_143005_Phone_Pixel8.wav"
    assert sync.voice_start(tmp_path / "phone" / name).hour == 14  # sync.pair_clips can line it up by time
    assert [f["name"] for f in c.get("/api/audio").json()] == [name]
    assert c.get("/api/audio/file", params={"name": name}).content[:4] == b"RIFF"
    assert c.get("/api/audio/file", params={"name": "../hub.json"}).status_code == 404
    assert c.post("/api/audio", content=b"", headers={"Content-Type": "audio/wav"}).status_code == 400


def test_browser_recordings_are_converted_to_wav(hub, tmp_path, monkeypatch):
    sf = pytest.importorskip("soundfile")
    h, base = hub
    monkeypatch.setattr(server, "PHONE_AUDIO", tmp_path / "phone")
    r = client(h, base, "iPhone").post("/api/audio", content=_wav_bytes(1.0), headers={"Content-Type": "audio/mp4"})
    assert r.status_code == 200, r.text
    info = sf.info(tmp_path / "phone" / r.json()["name"])
    assert (info.samplerate, info.subtype) == (48000, "PCM_24") and round(info.duration) == 1
    assert list((tmp_path / "phone").iterdir()) == [tmp_path / "phone" / r.json()["name"]]  # upload removed
