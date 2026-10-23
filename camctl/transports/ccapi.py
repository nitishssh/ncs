"""Wi-Fi control via Canon CCAPI (HTTP/JSON). Works on macOS and Windows."""
from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from camctl.core import Cancelled, CameraInfo, Transport

# Short names -> CCAPI shooting setting paths
SETTINGS = {
    "iso": "iso", "av": "av", "tv": "tv", "wb": "wb",
    "exposure": "exposure", "mode": "shootingmodedial", "af": "afoperation",
    "movie_size": "moviequality",
    "af_method": "afmethod",
    "drive": "drive",
    "picture_style": "picturestyle",
    "canon_log": "canonlog",                  # unverified path
    "log_assist": "viewassist",               # unverified path
    "hdr_pq": "hdrpq",                        # unverified path
}
# Verified on R50 V fw 1.2.0 (2026-10-23). Not available on this body: movierecordingformat,
# moviemode setting, movieservoaf, shootingmodedial - those keys are left out so the UI disables them.
ZOOM_PATH = "ver100/shooting/control/powerzoom"            # POST {"value": "wide"|"tele"|"stop"}
ZOOM_SPEED_PATH = "ver100/shooting/settings/powerzoom"     # {"speed": "slow"|"fast", "level": 1..15}
AF_POINT_PATH = "ver100/shooting/liveview/afframeposition"  # PUT {"positionx", "positiony"} in sensor pixels
CONTENTS = "ver140/contents"
STORAGE = "ver110/devicestatus/storage"
EVENTS = "ver110/event/polling"


class CCAPITransport(Transport):
    name = "wifi"

    def __init__(self, host: str = "192.168.1.2", port: int = 8080, https: bool = False, http_transport=None):
        """http_transport: optional httpx transport (tests plug in a fake R50 V here)."""
        self._http_transport = http_transport
        scheme = "https" if https else "http"
        self.base = f"{scheme}://{host}:{port}/ccapi"
        self.client: httpx.Client | None = None
        self.endpoints: set[str] = set()

    def _c(self) -> httpx.Client:
        if not self.client:
            raise RuntimeError("not connected")
        return self.client

    def connect(self) -> None:
        # The R50 V's API hangs (then answers 502 until power-cycled) under many parallel requests.
        # Cap it: 2 connections for commands/live view/thumbnails/downloads, 1 separate one for events.
        timeout = httpx.Timeout(10, connect=3)  # fail fast when the camera is off
        extra = {"transport": self._http_transport} if self._http_transport else {}
        self.client = httpx.Client(timeout=timeout, verify=False, **extra,
                                   limits=httpx.Limits(max_connections=2, max_keepalive_connections=2))
        self.events_client = httpx.Client(timeout=timeout, verify=False, **extra,
                                          limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        r = self.client.get(self.base)
        r.raise_for_status()
        # Discover which endpoints this body/firmware actually supports
        # Some bodies (R50 V fw 1.2.0) answer {"value": "No list of APIs"} instead of the list
        for ver, items in r.json().items():
            if isinstance(items, list):
                self.endpoints.update(item["path"] for item in items if isinstance(item, dict))

    def disconnect(self) -> None:
        if self.client:
            self.client.close()
            self.client = None
        if getattr(self, "events_client", None):
            self.events_client.close()
            self.events_client = None

    def info(self) -> CameraInfo:
        d = self._c().get(f"{self.base}/ver100/deviceinformation").json()
        return CameraInfo(d.get("productname", "?"), d.get("serialnumber", ""),
                          d.get("firmwareversion", ""), d)

    def shoot(self, autofocus: bool = True) -> None:
        r = self._c().post(f"{self.base}/ver100/shooting/control/shutterbutton", json={"af": autofocus})
        if r.status_code == 503 and "Mode not supported" in r.text:
            # Photo/movie is the physical switch on the R50 V; the API cannot change it
            raise RuntimeError("camera is in movie mode - set the photo/movie switch to photo to take stills")
        r.raise_for_status()

    def get_setting(self, key: str) -> dict:
        path = SETTINGS.get(key, key)
        r = self._c().get(f"{self.base}/ver100/shooting/settings/{path}")
        r.raise_for_status()
        return r.json()

    def set_setting(self, key: str, value: str) -> None:
        path = SETTINGS.get(key, key)
        self._c().put(f"{self.base}/ver100/shooting/settings/{path}",
                      json={"value": value}).raise_for_status()

    def start_liveview(self) -> None:
        self._c().post(f"{self.base}/ver100/shooting/liveview",
                       json={"liveviewsize": "small", "cameradisplay": "on"}).raise_for_status()
        # The first /flip returns 503 "Live view not started" for a moment after starting
        for _ in range(20):
            if self._c().get(f"{self.base}/ver100/shooting/liveview/flip").status_code == 200:
                break
            time.sleep(0.1)

    def liveview_frame(self) -> bytes:
        r = self._c().get(f"{self.base}/ver100/shooting/liveview/flip")
        # The R50 V answers 503 "Device busy" on ~15% of polls at ~18 fps; reuse the last frame
        if r.status_code == 503 and getattr(self, "_last_frame", None):
            return self._last_frame
        r.raise_for_status()
        self._last_frame = r.content
        return r.content

    def list_files(self) -> list[str]:
        c = self._c()
        files: list[str] = []
        url = lambda path: f"{self.base}{path.removeprefix('/ccapi')}"
        # card1 -> folders (DCIM/100CANON, XFVC/REEL_0001) -> files
        for storage in c.get(f"{self.base}/{CONTENTS}").json().get("path", []):
            for folder in c.get(url(storage)).json().get("path", []):
                # Folders are paged, 100 entries per page
                pages = c.get(url(folder), params={"kind": "number"}).json().get("pagenumber", 1)
                for page in range(1, pages + 1):
                    files += c.get(url(folder), params={"page": page}).json().get("path", [])
        return files

    def _url(self, remote: str) -> str:
        return f"{self.base}{remote.removeprefix('/ccapi')}"

    def download(self, remote: str, dest: Path, progress=None, cancel=None) -> Path:
        dest.mkdir(parents=True, exist_ok=True)
        out = dest / remote.rsplit("/", 1)[-1]
        part = out.with_name(out.name + ".part")
        # HEAD is not supported (405); ?kind=info gives the exact size on the card
        total = self._c().get(self._url(remote), params={"kind": "info"}).json().get("filesize", 0)
        done = 0
        with self._c().stream("GET", self._url(remote), timeout=None) as r:
            r.raise_for_status()
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

    def thumbnail(self, remote: str) -> bytes:
        r = self._c().get(self._url(remote), params={"kind": "thumbnail"})
        r.raise_for_status()
        return r.content

    def events(self, timeout: float = 10) -> dict:
        # The first poll returns the full camera state; later polls return only changes.
        # The camera holds "short" polls ~10 s and "long" polls ~30 s when nothing happens.
        wait = "short" if timeout <= 12 else "long"
        params = {"timeout": wait} if getattr(self, "_events_primed", False) else None
        try:
            r = self.events_client.get(f"{self.base}/{EVENTS}", params=params, timeout=(12 if wait == "short" else 35))
        except httpx.TimeoutException:
            return {}
        self._events_primed = True
        raw = r.json() if r.status_code == 200 and r.content else {}
        return self._normalize_events(raw)

    def _normalize_events(self, raw: dict) -> dict:
        out: dict = {}
        if raw.get("addedcontents"):
            out["added"] = raw["addedcontents"]
        by_api = {v: k for k, v in SETTINGS.items()}
        settings = {by_api[k]: v for k, v in raw.items() if k in by_api and isinstance(v, dict)}
        if settings:
            out["settings"] = settings
        eff = {k.removeprefix("effective_value_"): v.get("value") for k, v in raw.items()
               if k.startswith("effective_value_") and isinstance(v, dict)}
        if eff:
            out["effective"] = eff
        status = {}
        if isinstance(raw.get("battery"), dict):
            lvl = raw["battery"].get("level")
            status["battery"] = {"full": 100, "high": 75, "half": 50, "quarter": 25, "low": 10}.get(str(lvl), lvl)
        try:
            status["card_free_gb"] = round(raw["storage"]["storagelist"][0]["spacesize"] / 1e9, 1)
        except (KeyError, IndexError, TypeError):
            pass
        if isinstance(raw.get("recordable"), dict) and raw["recordable"].get("recordableshots") is not None:
            status["shots_left"] = raw["recordable"]["recordableshots"]
        if isinstance(raw.get("temperature"), dict):
            status["temp"] = raw["temperature"].get("status", "normal")
        if status:
            out["status"] = status
        return out

    def wait_new_files(self, timeout: float = 30) -> list[str]:
        return self.events(timeout).get("added", [])

    def record(self, start: bool) -> None:
        self._c().post(f"{self.base}/ver100/shooting/control/recbutton",
                       json={"action": "start" if start else "stop"}).raise_for_status()
        # recbutton cannot be read back on the R50 V (405), so time the take locally
        self._rec_since = time.time() if start else None

    def half_press(self) -> None:
        url = f"{self.base}/ver100/shooting/control/shutterbutton/manual"
        self._c().post(url, json={"action": "half_press", "af": True}).raise_for_status()
        self._c().post(url, json={"action": "release", "af": True}).raise_for_status()

    def liveview_info(self) -> dict:
        """AF frames, histogram and the live view's area on the sensor."""
        raw = self._c().get(f"{self.base}/ver100/shooting/liveview/flipdetail", params={"kind": "info"}).content
        body = raw[raw.index(b'{"liveviewdata"'):]
        return json.loads(body[:body.rindex(b"}") + 1])["liveviewdata"]

    def focus_at(self, x: float, y: float) -> None:
        # Live view shows a 16:9 strip of the 5400x3600 sensor; map the click into sensor pixels.
        # Moves the point in 1-point/spot/zone AF; in whole-area AF the camera picks the subject itself.
        img = self.liveview_info()["image"]
        px = round(img["positionx"] + x * img["positionwidth"])
        py = round(img["positiony"] + y * img["positionheight"])
        self._c().put(f"{self.base}/{AF_POINT_PATH}", json={"positionx": px, "positiony": py}).raise_for_status()
        self.half_press()

    def zoom_move(self, direction: str) -> None:
        """Power zoom is continuous on the R50 V: 'wide' or 'tele' until 'stop'."""
        self._c().post(f"{self.base}/{ZOOM_PATH}", json={"value": direction}).raise_for_status()

    def status(self) -> dict:
        c = self._c()
        bat = c.get(f"{self.base}/ver100/devicestatus/battery").json()
        # The R50 V reports a word ("full", "high", "half", "quarter", "low"), others a number
        level = bat.get("level")
        words = {"full": 100, "high": 75, "half": 50, "quarter": 25, "low": 10}
        out = {"battery": words.get(str(level), level), "temp": "normal",
               "recording": int(time.time() - self._rec_since) if getattr(self, "_rec_since", None) else None}
        try:
            st = c.get(f"{self.base}/{STORAGE}").json()["storagelist"][0]
            out["card_free_gb"] = round(st.get("spacesize", 0) / 1e9, 1)
        except Exception:
            pass
        try:
            out["temp"] = c.get(f"{self.base}/ver100/devicestatus/temperature").json().get("status", "normal")
        except Exception:
            pass
        return out

    def poll_events(self) -> dict:
        """Long-poll for setting/capture changes."""
        return self._c().get(f"{self.base}/{EVENTS}",
                             params={"timeout": "long"}, timeout=40).json()
