"""Hub + hub transport with the mock camera: pairing, shared state across devices, the camera limit."""
from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

pytest.importorskip("aiohttp")
from aiohttp import web  # noqa: E402

from camctl.hub import server  # noqa: E402
from camctl.transports.hub import HubTransport, parse_link  # noqa: E402


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
        if httpx.get(base + "/api/state", headers={"X-Camctl-Token": h.token}).json()["connection"] == "connected":
            break
        time.sleep(0.05)
    yield h, base
    asyncio.run_coroutine_threadsafe(runner.cleanup(), loop).result(5)
    loop.call_soon_threadsafe(loop.stop)


def client(h, base, device):
    return httpx.Client(base_url=base, headers={"X-Camctl-Token": h.token, "X-Camctl-Device": device}, timeout=10)


def test_requires_pairing_token(hub):
    h, base = hub
    assert httpx.get(base + "/api/state").status_code == 401
    assert httpx.get(base + "/api/state", headers={"X-Camctl-Token": "wrong"}).status_code == 401
    page = httpx.get(base + "/")
    assert page.status_code == 401 and "Pair this device" in page.text


def test_qr_link_sets_cookie(hub):
    h, base = hub
    r = httpx.get(base + f"/?token={h.token}")
    assert r.status_code == 200 and "camctl_token" in r.cookies
    assert httpx.get(base + "/api/state", cookies={"camctl_token": h.token}).status_code == 200


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
