"""Connect the Studio Utility app to a Studio Utility Hub instead of the camera, so every device shares one camera:
the hub owns the camera connection; this transport speaks the hub's HTTP API.

Address: the pairing link from the hub (http://<hub>:8765/?token=...), as shown under the QR code."""
from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx

from studio_utility.core import Cancelled, CameraInfo, NotSupported, Transport


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")


def local_token() -> str:
    """The hub's own secret from this Mac's hub.json: a hub on the same computer needs no pairing link."""
    from studio_utility.paths import CONFIG_DIR
    try:
        return json.loads((CONFIG_DIR / "hub.json").read_text()).get("token", "")
    except (OSError, ValueError):
        return ""


def parse_link(link: str) -> tuple[str, str]:
    """'http://10.63.196.242:8765/?token=abc' -> ('http://10.63.196.242:8765', 'abc').
    Empty or token-less 'localhost' -> the hub on this computer, with its token read from disk."""
    if "://" not in link:
        link = "http://" + (link or "localhost")
    u = urlparse(link)
    token = (parse_qs(u.query).get("token") or [""])[0] or u.fragment
    host = u.hostname or "localhost"
    if not token and host in LOCAL_HOSTS:
        token = local_token()
    port = u.port or 8765
    return f"{u.scheme}://{host}:{port}", token


def discover(timeout: float = 2.0) -> list[str]:
    """Hub base URLs announced on the local network via Bonjour (_studio-utility._tcp)."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    found: list[str] = []

    class Listener:
        def add_service(self, zc, kind, name):
            info = zc.get_service_info(kind, name, 1500)
            if info:
                for addr in info.addresses:
                    found.append(f"http://{socket.inet_ntoa(addr)}:{info.port}")

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass
    zc = Zeroconf()
    try:
        for kind in ("_studio-utility._tcp.local.", "_camctl._tcp.local."):  # current + pre-rename hubs
            ServiceBrowser(zc, kind, Listener())
        time.sleep(timeout)
    finally:
        zc.close()
    return sorted(set(found))


class HubTransport(Transport):
    name = "hub"

    def __init__(self, link: str = "", device: str | None = None):
        self.base, self.token = parse_link(link)
        self.device = device or f"{socket.gethostname().split('.')[0]} (Studio Utility)"
        self.client: httpx.Client | None = None
        self.events_client: httpx.Client | None = None
        self.state: dict = {}
        self.seq = 0

    def _headers(self):
        return {"X-Studio-Token": self.token, "X-Studio-Device": self.device}

    def _c(self) -> httpx.Client:
        if not self.client:
            raise RuntimeError("not connected")
        return self.client

    def _call(self, method: str, path: str, **kw):
        r = self._c().request(method, path, **kw)
        if r.status_code == 401:
            raise RuntimeError("hub refused the token: paste the pairing link from the hub's QR page")
        if r.status_code == 400:
            raise NotSupported(r.text)
        if r.status_code >= 400:
            raise RuntimeError(r.text or f"hub error {r.status_code}")
        return r

    # lifecycle
    def connect(self) -> None:
        timeout = httpx.Timeout(15, connect=3)
        self.client = httpx.Client(base_url=self.base, headers=self._headers(), timeout=timeout)
        self.events_client = httpx.Client(base_url=self.base, headers=self._headers(), timeout=timeout)
        self.state = self._call("GET", "/api/state").json()
        self.seq = self.state.get("seq", 0)
        if self.state.get("connection") != "connected":
            raise RuntimeError(f"hub is up, camera: {self.state.get('connection')}")

    def disconnect(self) -> None:
        for c in (self.client, self.events_client):
            if c:
                c.close()
        self.client = self.events_client = None

    def capabilities(self) -> set[str]:
        # what the camera behind the hub supports, limited to what this transport forwards
        return set(self.state.get("caps", [])) & (super().capabilities() | {"events"})

    def info(self) -> CameraInfo:
        i = self.state.get("info", {})
        return CameraInfo(model=f"{i.get('model', 'camera')} via hub", firmware=i.get("firmware", ""))

    # camera actions
    def shoot(self, autofocus: bool = True) -> None:
        self._call("POST", "/api/shoot", json={})

    def record(self, start: bool) -> None:
        self._call("POST", "/api/record", json={"start": start})

    def zoom(self, position: int) -> None:
        self._call("POST", "/api/zoom", json={"position": position})

    def zoom_move(self, direction: str) -> None:
        self._call("POST", "/api/zoom", json={"direction": direction})

    def focus_at(self, x: float, y: float) -> None:
        self._call("POST", "/api/focus", json={"x": x, "y": y})

    def half_press(self) -> None:
        self._call("POST", "/api/focus", json={})

    def focus_drive(self, step: int) -> None:
        self._call("POST", "/api/focus", json={"step": step})

    def get_setting(self, key: str) -> dict:
        return self._call("GET", f"/api/settings/{key}").json()

    def set_setting(self, key: str, value: str) -> None:
        self._call("PUT", f"/api/settings/{key}", json={"value": value})

    def status(self) -> dict:
        self.state = {**self.state, **self._call("GET", "/api/state").json()}
        return dict(self.state.get("status", {}))

    # live view
    def liveview_frame(self) -> bytes:
        return self._call("GET", "/api/live.jpg").content

    def liveview_info(self) -> dict:
        return self._call("GET", "/api/live/info").json()

    # files
    def list_files(self) -> list[str]:
        return self._call("GET", "/api/files").json()

    def thumbnail(self, remote: str) -> bytes:
        return self._call("GET", "/api/thumb", params={"path": remote}).content

    def download(self, remote: str, dest: Path, progress=None, cancel=None) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / remote.rsplit("/", 1)[-1]
        part = out.with_name(out.name + ".part")
        done = 0
        with self._c().stream("GET", "/api/file", params={"path": remote}, timeout=None) as r:
            if r.status_code >= 400:
                raise RuntimeError(f"hub: {r.read().decode(errors='replace')}")
            total = int(r.headers.get("content-length", 0))
            with part.open("wb") as f:
                for chunk in r.iter_bytes(1 << 20):
                    if cancel and cancel():
                        f.close()
                        part.unlink(missing_ok=True)
                        raise Cancelled(remote)
                    f.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, total)
        if total and done != total:
            part.unlink(missing_ok=True)
            raise IOError(f"incomplete download: {done} of {total} bytes")
        part.replace(out)
        return out

    # shared live state: the hub's event log, merged into the app's normalized event format
    def events(self, timeout: float = 10) -> dict:
        try:
            r = self.events_client.get("/api/events", params={"since": self.seq, "timeout": timeout},
                                       timeout=timeout + 10)
        except httpx.TimeoutException:
            return {}
        if r.status_code != 200:
            raise RuntimeError(f"hub events: {r.status_code}")
        body = r.json()
        self.seq = body["seq"]
        if body.get("reset"):
            s = body["state"]
            return {k: s[k] for k in ("settings", "status", "effective") if s.get(k)} | {"recording": s.get("recording")}
        out: dict = {}
        for ev in body["events"]:
            for k in ("settings", "status", "effective"):
                if ev.get(k):
                    out.setdefault(k, {}).update(ev[k])
            if ev.get("added"):
                out.setdefault("added", []).extend(ev["added"])
            if "recording" in ev:
                out["recording"] = ev["recording"]
                out["by"] = ev.get("by")
            if ev.get("connection") and ev["connection"] != "connected":
                out["camera_lost"] = ev["connection"]
        return out

    def wait_new_files(self, timeout: float = 30) -> list[str]:
        return self.events(timeout).get("added", [])
