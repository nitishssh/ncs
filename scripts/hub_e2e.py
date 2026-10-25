"""End-to-end check of a running Studio Hub and its camera, from any computer on the network.

    python scripts/hub_e2e.py http://10.11.0.2:8765 <token>

Read-mostly: every setting it changes is set back to its original value. It never shoots or records.
Covers what the phone and desktop apps use: connection, live view, settings, focus, card files, download."""
from __future__ import annotations

import sys
import time

import httpx

USB_ONLY = ("zoom_speed", "color_temp", "wb_shift_ab", "wb_shift_gm", "color_filter", "clip_name")


def main(base: str, token: str) -> int:
    c = httpx.Client(base_url=base.rstrip("/"), headers={"X-Studio-Token": token, "X-Studio-Device": "e2e test"},
                     timeout=30)
    results: list[tuple[str, bool, str]] = []

    def check(name, fn):
        t = time.time()
        try:
            detail = fn() or ""
            results.append((name, True, f"{detail} ({time.time() - t:.1f}s)"))
        except Exception as e:  # report and keep going
            results.append((name, False, f"{type(e).__name__}: {e}"))

    state = c.get("/api/state").json()
    if state.get("connection") != "connected":
        print(f"Hub has no camera: {state.get('connection')}")
        return 2
    caps, settings = set(state.get("caps", [])), state.get("settings", {})
    print(f"Camera: {state['info'].get('model')}  firmware {state['info'].get('firmware')}")
    print(f"Link capabilities: {', '.join(sorted(caps))}\n")

    def live():
        frames = [c.get("/api/live.jpg").content for _ in range(5)]
        assert all(f[:2] == b"\xff\xd8" for f in frames), "not JPEG"
        return f"5 frames, {len(frames[-1]) // 1024} KB each"
    check("live view", live)
    check("live view, Canon Log 3 → Rec.709 preview",
          lambda: f"{len(c.get('/api/live.jpg', params={'lut': 'clog3'}).content) // 1024} KB")

    def round_trip(key):
        def go():
            d = c.get(f"/api/settings/{key}").json()
            before, options = d["value"], d.get("ability") or []
            others = [o for o in options if o != before]
            if not others:
                return f"{before} (locked in this mode)"
            target = others[len(others) // 2]
            after = c.put(f"/api/settings/{key}", json={"value": target}).json()["value"]
            back = c.put(f"/api/settings/{key}", json={"value": before}).json()["value"]
            assert after == target and back == before, f"{before}->{after}->{back}"
            return f"{before} → {target} → {back}"
        return go
    for key in ("iso", "tv", "av", "exposure", "wb", "picture_style") + USB_ONLY:
        if key in settings:
            check(f"setting {key}", round_trip(key))

    if "half_press" in caps:
        check("autofocus", lambda: c.post("/api/focus", json={}).raise_for_status() and "")
    if "focus_drive" in caps:
        check("focus drive far/near", lambda: [c.post("/api/focus", json={"step": s}).raise_for_status() for s in (1, -1)]
              and "")
    if "status" in caps:
        check("status", lambda: str(c.get("/api/state").json().get("status")))

    files: list[str] = []

    def listing():
        files.extend(c.get("/api/files").json())
        return f"{len(files)} files"
    check("card file list", listing)
    if files:
        check("thumbnail", lambda: f"{len(c.get('/api/thumb', params={'path': files[-1]}).content) // 1024} KB")
        small = next((f for f in reversed(files) if f.upper().endswith(".JPG")), files[-1])

        def download():
            r = c.get("/api/file", params={"path": small}, timeout=300)
            r.raise_for_status()
            return f"{small.rsplit('/', 1)[-1]} {len(r.content) / 1e6:.1f} MB"
        check("download original", download)

    width = max(len(n) for n, _, _ in results)
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name:<{width}}  {detail}")
    failed = sum(not ok for _, ok, _ in results)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
