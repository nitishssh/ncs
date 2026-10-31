"""Lighting simulator: exposure maths, WB choice, preview and the UI tab on the mock camera."""
from __future__ import annotations

import math

from studio_utility.lighting import Light, Scene, kelvin_rgb, parse_av, parse_tv, render, suggest
from tests.test_ui import app, spin, win  # noqa: F401  (fixtures)


def test_parse_camera_strings():
    assert parse_tv("1/125") == 1 / 125
    assert parse_tv('0"5') == 0.5 and parse_tv('1"') == 1
    assert parse_av("f4.0") == 4 and parse_av("5.6") == 5.6


def test_inverse_square_and_back_light_ignored():
    near = Scene([Light("Key", 1000, 1.0, 0, 0)], ambient_lux=0)
    far = Scene([Light("Key", 1000, 2.0, 0, 0)], ambient_lux=0)
    assert math.isclose(near.subject_lux() / far.subject_lux(), 4)
    back = Scene([Light("Back", 5000, 1.0, 180, 0)], ambient_lux=10)
    assert back.subject_lux() < 11


def test_suggestion_matches_exposure_equation():
    scene = Scene([Light("Key", 640, 1.0, 0, 0)], ambient_lux=0)  # 640 lux -> EV100 8
    s = suggest(scene, 4.0, fps=None)
    assert (s.tv, s.av) == ("1/125", "f4.0")
    assert s.iso == "800"  # 100 * 4² * 125 / 2⁸ = 781
    assert abs(s.error_stops) <= 0.6


def test_movie_locks_180_degree_shutter_and_warns_when_dark():
    s = suggest(Scene([Light("Key", 50, 3.0, 0, 0)], ambient_lux=0), 8.0, fps=29.97)
    assert s.tv == "1/60"
    assert any("Too dark" in n for n in s.notes)


def test_wb_follows_lights():
    tungsten = Scene([Light("Key", 2000, 1, 0, 0, 3200)], ambient_lux=0)
    assert suggest(tungsten, 4).wb == "tungsten"
    s = suggest(tungsten, 4, choices={"color_temp": ["2500", "3200", "5600"], "wb": ["auto", "colortemp"]})
    assert (s.wb, s.color_temp) == ("colortemp", "3200")
    assert kelvin_rgb(3200)[2] < kelvin_rgb(6500)[2]


def test_render_brightens_with_overexposure():
    scene = Scene()
    assert render(scene, 5600, 1.0).mean() > render(scene, 5600, 0.0).mean() > render(scene, 5600, -1.0).mean()


def test_lighting_tab_sends_to_mock(win, app):  # noqa: F811
    tab = win.lighting
    for _ in range(50):
        if win.boxes["iso"].count():
            break
        spin(app, 0.1)
    assert tab.apply_btn.isEnabled()
    assert not tab.preview.pixmap().isNull()
    tab.rows[0][1].setValue(4000)
    assert "ISO" in tab.readout.text()
    tab.apply_btn.click()
    spin(app, 0.3)
    s = tab.result
    assert win.t.values["mode"] == "M"
    assert (win.t.values["tv"], win.t.values["av"], win.t.values["iso"]) == (s.tv, s.av, s.iso)
