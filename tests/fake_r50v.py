"""A fake Canon EOS R50 V (firmware 1.2.0) for tests, replaying replies recorded from the real camera
on 2026-10-23. Plug it into CCAPITransport(http_transport=FakeR50V().transport)."""
from __future__ import annotations

import json

import httpx

V = "/ccapi"
CARD = f"{V}/ver140/contents/card1"
PHOTOS = f"{CARD}/DCIM/100CANON"
MOVIES = f"{CARD}/XFVC/REEL_0001"


class FakeR50V:
    def __init__(self, n_photos: int = 150, movie_mode: bool = False, battery: str = "half"):
        self.photos = [f"{PHOTOS}/IMG_{i:04d}.JPG" for i in range(1, n_photos + 1)]
        self.movies = [f"{MOVIES}/A_0001C034A261023_190839BM_CANON.MP4"]
        self.movie_mode = movie_mode
        self.battery = battery
        self.settings = {
            "iso": {"value": "auto", "ability": ["auto"]},
            "wb": {"value": "auto", "ability": ["auto", "awbwhite", "daylight", "shade"]},
            "exposure": {"value": "+0.0", "ability": ["-1.0", "-0_2/3", "-0_1/3", "+0.0", "+0_1/3", "+0_2/3", "+1.0"]},
            "moviequality": {"value": "1920x1080_2500_longgop_standard",
                             "ability": ["3840x2160_5000_longgop_standard_crop", "1920x1080_2500_longgop_standard"]},
        }
        self.liveview_on = False
        self.flip_calls = 0
        self.zoom_cmds: list[str] = []
        self.af_puts: list[dict] = []
        self.shots = 0
        self.events = [  # queued /event/polling replies after the first (full state) one
            {"effective_value_tv": {"value": '0"3'}, "effective_value_iso": {"value": "1600"}},
            {"addedcontents": [f"{PHOTOS}/IMG_0164.CR3", f"{PHOTOS}/IMG_0164.JPG"]},
        ]
        self.event_calls = 0
        self.transport = httpx.MockTransport(self.handle)

    @staticmethod
    def j(data, status=200):
        return httpx.Response(status, content=json.dumps(data).encode(), headers={"content-type": "application/json"})

    def file_bytes(self, path: str) -> bytes:
        return (path.encode() * 400)[:12345]

    def handle(self, req: httpx.Request) -> httpx.Response:
        p, q, m = req.url.path, dict(req.url.params), req.method
        if p == V:
            return self.j({"value": "No list of APIs"})
        if p == f"{V}/ver100/deviceinformation":
            return self.j({"manufacturer": "Canon.Inc", "productname": "Canon EOS R50 V", "serialnumber": "159802206576",
                           "macaddress": "7c:b8:da:30:f7:48", "firmwareversion": "1.2.0"})
        if p == f"{V}/ver100/devicestatus/battery":
            return self.j({"kind": "battery", "name": "LP-E17", "quality": "good", "level": self.battery})
        if p == f"{V}/ver100/devicestatus/storage":
            return httpx.Response(404)  # only ver110 exists on this body
        if p == f"{V}/ver110/devicestatus/storage":
            return self.j({"storagelist": [{"name": "card1", "path": CARD, "spacesize": 118186180608, "contentsnumber": 174}]})
        if p == f"{V}/ver100/devicestatus/temperature":
            return self.j({"status": "normal"})
        if p == f"{V}/ver140/contents":
            return self.j({"path": [CARD]})
        if p == CARD:
            return self.j({"path": [PHOTOS, MOVIES]})
        if p in (PHOTOS, MOVIES):
            items = self.photos if p == PHOTOS else self.movies
            pages = max(1, -(-len(items) // 100))
            if q.get("kind") == "number":
                return self.j({"contentsnumber": len(items), "pagenumber": pages})
            page = int(q.get("page", 1))
            return self.j({"path": items[(page - 1) * 100: page * 100]})
        if p.startswith(CARD + "/"):
            if q.get("kind") == "info":
                return self.j({"filesize": len(self.file_bytes(p))})
            if q.get("kind") == "thumbnail":
                return httpx.Response(200, content=b"\xff\xd8thumb\xff\xd9", headers={"content-type": "image/jpeg"})
            return httpx.Response(200, content=self.file_bytes(p))
        if p.startswith(f"{V}/ver100/shooting/settings/"):
            key = p.rsplit("/", 1)[-1]
            if key not in self.settings:
                return httpx.Response(404)
            if m == "PUT":
                v = json.loads(req.content)["value"]
                if v not in self.settings[key]["ability"]:
                    return self.j({"message": "Invalid parameter"}, 400)
                self.settings[key]["value"] = v
                return self.j({})
            return self.j(self.settings[key])
        if p == f"{V}/ver100/shooting/liveview" and m == "POST":
            self.liveview_on = True
            return self.j({})
        if p == f"{V}/ver100/shooting/liveview/flip":
            self.flip_calls += 1
            if not self.liveview_on:
                return self.j({"message": "Live view not started"}, 503)
            if self.flip_calls % 3 == 0:  # the real camera answers "Device busy" on ~15% of polls
                return self.j({"message": "Device busy"}, 503)
            return httpx.Response(200, content=b"\xff\xd8frame%d\xff\xd9" % self.flip_calls)
        if p == f"{V}/ver100/shooting/liveview/flipdetail":
            info = {"liveviewdata": {"histogram": [[1] * 256] * 4, "afframe": [],
                                     "image": {"positionx": 0, "positiony": 281, "positionwidth": 5400,
                                               "positionheight": 3037, "sizex": 5400, "sizey": 3600}}}
            return httpx.Response(200, content=b"\x00\x01binary-header" + json.dumps(info).encode() + b"\xff")
        if p == f"{V}/ver100/shooting/liveview/afframeposition" and m == "PUT":
            self.af_puts.append(json.loads(req.content))
            return self.j({})
        if p == f"{V}/ver100/shooting/control/powerzoom" and m == "POST":
            self.zoom_cmds.append(json.loads(req.content)["value"])
            return self.j({})
        if p == f"{V}/ver100/shooting/control/shutterbutton" and m == "POST":
            if self.movie_mode:
                return self.j({"message": "Mode not supported"}, 503)
            self.shots += 1
            return self.j({})
        if p == f"{V}/ver100/shooting/control/shutterbutton/manual" and m == "POST":
            return self.j({})
        if p == f"{V}/ver100/shooting/control/recbutton":
            return self.j({}) if m == "POST" else httpx.Response(405)
        if p == f"{V}/ver110/event/polling":
            self.event_calls += 1
            if self.event_calls == 1:  # first poll: full state
                return self.j({"battery": {"level": self.battery}, "recordable": {"recordableshots": 2959},
                               "storage": {"storagelist": [{"spacesize": 118000000000}]}})
            return self.j(self.events.pop(0) if self.events else {})
        return httpx.Response(404)
