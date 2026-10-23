"""CCAPI transport against the recorded R50 V behaviour. Each test pins one quirk found on the real camera."""
from __future__ import annotations

import pytest

from camctl.core import Cancelled
from camctl.transports.ccapi import CCAPITransport
from tests.fake_r50v import PHOTOS, FakeR50V


@pytest.fixture
def cam():
    return FakeR50V()


@pytest.fixture
def t(cam):
    tr = CCAPITransport(host="10.11.0.2", port=8090, http_transport=cam.transport)
    tr.connect()
    yield tr
    tr.disconnect()


def test_connects_without_api_list(t):
    # fw 1.2.0 answers /ccapi with {"value": "No list of APIs"} instead of the endpoint list
    assert t.info().model == "Canon EOS R50 V"
    assert t.endpoints == set()


@pytest.mark.parametrize("word,pct", [("full", 100), ("half", 50), ("low", 10)])
def test_battery_word_becomes_percent(cam, word, pct):
    cam.battery = word
    tr = CCAPITransport(http_transport=cam.transport)
    tr.connect()
    assert tr.status()["battery"] == pct


def test_card_space_uses_ver110_storage(t):
    assert t.status()["card_free_gb"] == 118.2


def test_file_list_reads_every_page(t, cam):
    files = t.list_files()
    assert len(files) == len(cam.photos) + len(cam.movies)  # 150 photos = 2 pages of 100
    assert files[0].endswith("IMG_0001.JPG") and f"{PHOTOS}/IMG_0150.JPG" in files


def test_download_is_byte_identical_with_progress(t, cam, tmp_path):
    remote = cam.photos[0]
    ticks = []
    out = t.download(remote, tmp_path, progress=lambda d, n: ticks.append((d, n)))
    assert out.read_bytes() == cam.file_bytes(remote)
    assert ticks[-1] == (len(cam.file_bytes(remote)),) * 2
    assert not list(tmp_path.glob("*.part"))


def test_cancelled_download_leaves_nothing(t, cam, tmp_path):
    with pytest.raises(Cancelled):
        t.download(cam.photos[0], tmp_path, cancel=lambda: True)
    assert list(tmp_path.iterdir()) == []


def test_incomplete_download_is_rejected(t, cam, tmp_path, monkeypatch):
    monkeypatch.setattr(cam, "file_bytes", lambda p: b"x" * 100 if "kind" not in p else b"")
    real = cam.handle

    def lie_about_size(req):
        if req.url.params.get("kind") == "info":
            return cam.j({"filesize": 999})
        return real(req)
    cam.transport.handler = lie_about_size
    with pytest.raises(IOError):
        t.download(cam.photos[0], tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_liveview_waits_for_start_and_survives_busy(t, cam):
    t.start_liveview()
    frames = [t.liveview_frame() for _ in range(9)]  # every 3rd poll is "Device busy" (503)
    assert all(f.startswith(b"\xff\xd8") for f in frames)


def test_power_zoom_is_continuous(t, cam):
    for d in ("tele", "stop", "wide", "stop"):
        t.zoom_move(d)
    assert cam.zoom_cmds == ["tele", "stop", "wide", "stop"]
    assert "zoom_move" in t.capabilities() and "zoom" not in t.capabilities()


def test_click_to_focus_maps_into_sensor_pixels(t, cam):
    t.focus_at(0.2, 0.5)  # live view is the 16:9 strip y=281..3318 of the 5400x3600 sensor
    assert cam.af_puts == [{"positionx": 1080, "positiony": round(281 + 0.5 * 3037)}]


def test_shoot_in_movie_mode_explains_the_switch():
    cam = FakeR50V(movie_mode=True)
    tr = CCAPITransport(http_transport=cam.transport)
    tr.connect()
    with pytest.raises(RuntimeError, match="photo/movie switch"):
        tr.shoot()


def test_events_are_normalized(t):
    first = t.events(1)
    assert first["status"] == {"battery": 50, "card_free_gb": 118.0, "shots_left": 2959}
    assert t.events(1) == {"effective": {"tv": '0"3', "iso": "1600"}}
    assert t.events(1)["added"][1].endswith("IMG_0164.JPG")


def test_unavailable_setting_raises(t):
    with pytest.raises(Exception):
        t.get_setting("movie_format")  # not on this body -> UI greys it out


def test_connection_limits_protect_the_camera(cam, monkeypatch):
    # The R50 V's API hangs under parallel requests: 2 connections for everything + 1 for events
    import httpx
    seen = []
    real = httpx.Client

    def spy(*a, **kw):
        seen.append(kw.get("limits"))
        return real(*a, **kw)
    monkeypatch.setattr(httpx, "Client", spy)
    tr = CCAPITransport(http_transport=cam.transport)
    tr.connect()
    assert [lim.max_connections for lim in seen] == [2, 1]
