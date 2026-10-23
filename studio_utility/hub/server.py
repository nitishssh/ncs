"""Studio Utility Hub: the one process that talks to the camera; phones, Macs and PCs connect to it.

- Owns the single camera connection and enforces the R50 V's limit: camera calls run on a 2-thread pool
  (+ one event listener), so any number of devices can never overload the camera's API.
- Shared live state: every change (camera events, recording started from a phone…) is appended to a
  sequence-numbered event log; clients long-poll /api/events?since=N and all stay in sync.
- Live view is fetched once and fanned out (MJPEG) to every viewer.
- Access needs the pairing token (QR code on the hub's page); it's remembered in a cookie.

Run:  python -m studio_utility.hub --camera 10.11.0.2:8090      (or --camera mock)
"""
from __future__ import annotations

import asyncio
import json
import secrets
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from aiohttp import web

from studio_utility import transports
from studio_utility.core import NotSupported
from studio_utility.paths import CONFIG_DIR, migrate

WEB = Path(__file__).parent / "web"
CONFIG = CONFIG_DIR / "hub.json"
CACHE = CONFIG_DIR / "hub-cache"
LOG_MAX = 500
LIVE_FPS = 15


def load_token() -> str:
    migrate()
    try:
        return json.loads(CONFIG.read_text())["token"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        token = secrets.token_urlsafe(18)
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps({"token": token}))
        return token


def lan_addresses() -> list[str]:
    """IPv4 addresses other devices can use to reach this hub."""
    out = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            out.add(info[4][0])
    except socket.gaierror:
        pass
    try:  # the address used for the default route (works even without DNS for the hostname)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        out.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    return sorted(a for a in out if not a.startswith(("127.", "169.254.")))


class Hub:
    def __init__(self, kind: str = "wifi", **camera_kwargs):
        self.kind, self.camera_kwargs = kind, camera_kwargs
        self.t = None
        self.caps: set[str] = set()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="camera")  # = the camera's limit
        self.token = load_token()
        self.seq = 0
        self.log: list[dict] = []
        self.state: dict = {"connection": "connecting", "info": {}, "status": {}, "settings": {},
                            "effective": {}, "recording": None, "caps": []}
        self.changed: asyncio.Condition | None = None
        self.frame: bytes | None = None
        self.frame_id = 0
        self.viewers = 0
        self.loop: asyncio.AbstractEventLoop | None = None
        self.stopping = False

    # ---- event log / shared state
    def publish(self, event: dict):
        """Thread-safe: record an event, merge it into the state, wake every waiting client."""
        def apply():
            self.seq += 1
            ev = {"seq": self.seq, "time": time.time(), **event}
            self.log.append(ev)
            del self.log[:-LOG_MAX]
            for key in ("status", "settings", "effective"):
                if key in event:
                    self.state[key] = {**self.state[key], **event[key]}
            for key in ("connection", "info", "recording", "caps"):
                if key in event:
                    self.state[key] = event[key]
            if event.get("added"):
                self.state["last_added"] = event["added"]

            async def wake():
                async with self.changed:
                    self.changed.notify_all()
            asyncio.ensure_future(wake())
        self.loop.call_soon_threadsafe(apply)

    @property
    def cam_t(self):
        if self.t is None:
            raise web.HTTPServiceUnavailable(text="camera not connected")
        return self.t

    async def cam(self, fn, *args):
        """Run a blocking camera call on the 2-thread pool."""
        if self.t is None:
            raise web.HTTPServiceUnavailable(text="camera not connected")
        return await self.loop.run_in_executor(self.pool, fn, *args)

    # ---- camera lifecycle (background threads)
    def connect_loop(self):
        while not self.stopping:
            if self.t is None:
                try:
                    t = transports.load(self.kind, **self.camera_kwargs)
                    t.connect()
                    caps = t.capabilities()
                    info = t.info()
                    settings = {}
                    for key in ("movie_size", "iso", "tv", "av", "exposure", "wb", "af_method", "picture_style"):
                        try:
                            settings[key] = t.get_setting(key)
                        except Exception:
                            pass
                    self.t, self.caps = t, caps
                    self.publish({"type": "connected", "connection": "connected", "caps": sorted(caps),
                                  "info": {"model": info.model, "firmware": info.firmware},
                                  "settings": settings, "status": self.safe_status()})
                except Exception as e:
                    self.publish({"type": "connection", "connection": f"waiting for camera ({str(e)[:60]})"})
                    time.sleep(5)
                    continue
            if "events" in self.caps:
                try:
                    ev = self.t.events(timeout=10)
                except Exception:
                    self.drop("camera stopped answering")
                    continue
                if ev:
                    self.publish({"type": "camera", **ev})
            else:
                time.sleep(5)
                self.publish({"type": "camera", "status": self.safe_status()})

    def safe_status(self) -> dict:
        try:
            return self.t.status() if self.t and "status" in self.caps else {}
        except Exception:
            return {}

    def drop(self, why: str):
        try:
            self.t.disconnect()
        except Exception:
            pass
        self.t, self.caps = None, set()
        self.publish({"type": "connection", "connection": f"lost: {why}", "recording": None})

    async def live_loop(self):
        """Fetch live view only while someone is watching; share each frame with everyone."""
        while not self.stopping:
            watching = self.viewers or time.time() < getattr(self, "jpg_viewer_until", 0)
            if watching and self.t is not None and "liveview_frame" in self.caps:
                try:
                    if self.frame is None and hasattr(self.t, "start_liveview"):
                        await self.cam(self.t.start_liveview)
                    self.frame = await self.cam(self.t.liveview_frame)
                    self.frame_id += 1
                    async with self.changed:
                        self.changed.notify_all()
                except Exception:
                    self.frame = None
                    await asyncio.sleep(1)
                await asyncio.sleep(1 / LIVE_FPS)
            else:
                self.frame = None
                await asyncio.sleep(0.25)

    # ---- web
    @web.middleware
    async def auth(self, request: web.Request, handler):
        if request.path in ("/manifest.webmanifest", "/icon.svg", "/sw.js") or request.path.startswith("/static/"):
            return await handler(request)
        token = (request.query.get("token") or request.headers.get("X-Studio-Token")
                 or request.cookies.get("studio_token")
                 # devices paired before the rename (camctl) keep working
                 or request.headers.get("X-Camctl-Token") or request.cookies.get("camctl_token"))
        local = request.remote in ("127.0.0.1", "::1")
        if not secrets.compare_digest(token or "", self.token) and not (local and request.path == "/pair"):
            if request.path.startswith("/api/"):
                raise web.HTTPUnauthorized(text="pair this device first (scan the QR code on the hub)")
            return web.Response(text=(WEB / "locked.html").read_text(), content_type="text/html", status=401)
        resp = await handler(request)
        if request.query.get("token"):  # first visit from the QR code: remember the device
            resp.set_cookie("studio_token", self.token, max_age=3600 * 24 * 365, httponly=True, samesite="Lax")
        return resp

    async def index(self, request):
        return web.FileResponse(WEB / "index.html")

    async def pair_page(self, request):
        """QR code for phones: open on the hub machine itself (http://localhost:8765/pair)."""
        import io

        import qrcode
        import qrcode.image.svg
        port = request.url.port or 8765
        urls = [f"http://{a}:{port}/?token={self.token}" for a in lan_addresses()]
        blocks = []
        for u in urls:
            buf = io.BytesIO()
            qrcode.make(u, image_factory=qrcode.image.svg.SvgPathImage, box_size=12).save(buf)
            blocks.append(f'<figure>{buf.getvalue().decode()}<figcaption>{u.split("?")[0]}'
                          f'<input readonly value="{u}" onclick="this.select()" title="Desktop app: copy this link">'
                          f'</figcaption></figure>')
        html = (WEB / "pair.html").read_text().replace("{{codes}}", "".join(blocks))
        return web.Response(text=html, content_type="text/html")

    async def api_state(self, request):
        return web.json_response({"seq": self.seq, **self.state, "server_time": time.time()})

    async def api_events(self, request):
        since = int(request.query.get("since", 0))
        timeout = min(float(request.query.get("timeout", 25)), 30)
        if self.seq <= since:
            async with self.changed:
                try:
                    await asyncio.wait_for(self.changed.wait_for(lambda: self.seq > since), timeout)
                except asyncio.TimeoutError:
                    pass
        events = [e for e in self.log if e["seq"] > since]
        if events and events[0]["seq"] > since + 1:  # client fell too far behind: give it the full state
            return web.json_response({"seq": self.seq, "reset": True, "state": self.state, "events": []})
        return web.json_response({"seq": self.seq, "events": events})

    async def act(self, request, name: str, fn, *args, event: dict | None = None):
        try:
            result = await self.cam(fn, *args)
        except NotSupported as e:
            raise web.HTTPBadRequest(text=str(e))
        except web.HTTPException:
            raise
        except Exception as e:
            raise web.HTTPBadGateway(text=f"{name}: {e}")
        if event:
            self.publish({"type": name, "by": request.headers.get("X-Studio-Device", "device"), **event})
        return web.json_response({"ok": True, "result": result if isinstance(result, (str, int, float, dict, list)) else None})

    async def api_shoot(self, request):
        return await self.act(request, "shoot", self.cam_t.shoot, event={})

    async def api_record(self, request):
        start = bool((await request.json()).get("start"))
        rec = {"since": time.time(), "by": request.headers.get("X-Studio-Device", "device")} if start else None
        return await self.act(request, "record", self.cam_t.record, start, event={"recording": rec})

    async def api_zoom(self, request):
        body = await request.json()
        if "direction" in body:
            return await self.act(request, "zoom", self.cam_t.zoom_move, body["direction"])
        return await self.act(request, "zoom", self.cam_t.zoom, int(body["position"]))

    async def api_focus(self, request):
        body = await request.json()
        if "x" in body:
            return await self.act(request, "focus", self.cam_t.focus_at, float(body["x"]), float(body["y"]))
        return await self.act(request, "focus", self.cam_t.half_press)

    async def api_setting(self, request):
        key = request.match_info["key"]
        if request.method == "PUT":
            value = (await request.json())["value"]
            await self.act(request, "setting", self.cam_t.set_setting, key, value)
            fresh = await self.cam(self.cam_t.get_setting, key)
            self.publish({"type": "setting", "settings": {key: fresh}})
            return web.json_response(fresh)
        return web.json_response(await self.cam(self.cam_t.get_setting, key))

    async def api_files(self, request):
        return web.json_response(await self.cam(self.cam_t.list_files))

    async def api_thumb(self, request):
        data = await self.cam(self.cam_t.thumbnail, request.query["path"])
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})

    async def api_file(self, request):
        """Original file, byte-for-byte (cached on the hub so several devices don't re-read the card)."""
        remote = request.query["path"]
        name = remote.rsplit("/", 1)[-1]
        cached = CACHE / name
        if not cached.exists():
            await self.cam(self.cam_t.download, remote, CACHE)
        return web.FileResponse(cached, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def api_live_jpg(self, request):
        """Latest shared frame. Polling clients count as viewers for 2 s after each request,
        so the hub keeps fetching from the camera only while someone is actually watching."""
        self.jpg_viewer_until = time.time() + 2
        for _ in range(40):
            if self.frame:
                break
            await asyncio.sleep(0.05)
        if not self.frame:
            raise web.HTTPServiceUnavailable(text="no live view")
        return web.Response(body=self.frame, content_type="image/jpeg", headers={"Cache-Control": "no-store"})

    async def api_live_mjpg(self, request):
        resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=frame",
                                           "Cache-Control": "no-store"})
        await resp.prepare(request)
        self.viewers += 1
        last = -1
        try:
            while not self.stopping:
                async with self.changed:
                    await self.changed.wait_for(lambda: self.frame_id != last or self.stopping)
                last, frame = self.frame_id, self.frame
                if frame:
                    await resp.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " +
                                     str(len(frame)).encode() + b"\r\n\r\n" + frame + b"\r\n")
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.viewers -= 1
        return resp

    async def api_live_info(self, request):
        return web.json_response(await self.cam(self.cam_t.liveview_info))

    def app(self) -> web.Application:
        app = web.Application(middlewares=[self.auth])
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/pair", self.pair_page)
        r.add_get("/api/state", self.api_state)
        r.add_get("/api/events", self.api_events)
        r.add_post("/api/shoot", self.api_shoot)
        r.add_post("/api/record", self.api_record)
        r.add_post("/api/zoom", self.api_zoom)
        r.add_post("/api/focus", self.api_focus)
        r.add_route("*", "/api/settings/{key}", self.api_setting)
        r.add_get("/api/files", self.api_files)
        r.add_get("/api/thumb", self.api_thumb)
        r.add_get("/api/file", self.api_file)
        r.add_get("/api/live.jpg", self.api_live_jpg)
        r.add_get("/api/live.mjpg", self.api_live_mjpg)
        r.add_get("/api/live/info", self.api_live_info)
        def serve(name):
            async def handler(request):
                return web.FileResponse(WEB / name)
            return handler
        for name in ("manifest.webmanifest", "icon.svg", "sw.js"):
            r.add_get(f"/{name}", serve(name))
        r.add_static("/static/", WEB)
        app.on_startup.append(self.on_startup)
        app.on_cleanup.append(self.on_cleanup)
        return app

    async def on_startup(self, app):
        self.loop = asyncio.get_running_loop()
        self.changed = asyncio.Condition()
        threading.Thread(target=self.connect_loop, daemon=True).start()
        self.live_task = asyncio.create_task(self.live_loop())

    async def on_cleanup(self, app):
        self.stopping = True
        self.live_task.cancel()
        async with self.changed:
            self.changed.notify_all()
        if self.t:
            try:
                self.t.disconnect()
            except Exception:
                pass
