"""Hub regressions found by /qa: file paths stay inside the cache, and bad request input is a 400, not a crash."""
from __future__ import annotations

import httpx
import pytest

pytest.importorskip("aiohttp")

from tests.test_hub import hub  # noqa: E402,F401  (shared mock-camera hub fixture)


# Regression: ISSUE-001 — /api/file resolved '..' and '..\' names outside the hub cache (Windows: the admin token file)
# Found by /qa on 2026-10-27
# Report: .gstack/qa-reports/qa-report-studio-utility-hub-2026-10-27.md
@pytest.mark.parametrize("path", ["DCIM/..", "DCIM/..\\hub.json", "..\\..\\hub.json", "DCIM/.hidden", "DCIM/"])
def test_file_names_cannot_leave_the_cache(hub, path):
    h, base = hub
    first = httpx.get(base + "/api/file?path=DCIM/IMG_0001.JPG", headers={"X-Studio-Token": h.token})  # creates the cache
    assert first.status_code == 200
    r = httpx.get(base + "/api/file", params={"path": path}, headers={"X-Studio-Token": h.token})
    assert r.status_code == 400
    assert h.token not in r.text and "studio-utility" not in r.text  # no secret, no local path in the reply


# Regression: ISSUE-002 — malformed request input crashed the handler with an unhandled 500
# Found by /qa on 2026-10-27
# Report: .gstack/qa-reports/qa-report-studio-utility-hub-2026-10-27.md
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/events?since=abc", None),
    ("GET", "/api/events?since=0&timeout=soon", None),
    ("POST", "/api/zoom", "{}"),
    ("POST", "/api/zoom", '{"position": "wide"}'),
    ("POST", "/api/record", "not json"),
    ("POST", "/api/focus", '{"x": "left", "y": 0.5}'),
    ("POST", "/api/focus", '{"x": 0.5}'),
    ("PUT", "/api/settings/iso", "{}"),
])
def test_bad_input_is_a_400(hub, method, path, body):
    h, base = hub
    r = httpx.request(method, base + path, content=body, headers={"X-Studio-Token": h.token})
    assert r.status_code == 400, r.text
    assert httpx.get(base + "/api/state", headers={"X-Studio-Token": h.token}).status_code == 200  # hub still fine
