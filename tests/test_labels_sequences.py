"""Readable labels and the shooting-tool engine (interval, bracketing, presets) on the mock camera."""
from __future__ import annotations

import time

import pytest

from studio_utility import sequences
from studio_utility.labels import label
from studio_utility.sequences import Bracket, Interval, apply_preset, exposure_steps, load_presets, save_preset
from studio_utility.transports.mock import ABILITY, MockTransport


@pytest.mark.parametrize("key,raw,shown", [
    ("movie_size", "3840x2160_5000_longgop_standard_crop", "4K Crop 50p"),
    ("movie_size", "1920x1080_10000_longgop_standard", "FHD 100p"),
    ("movie_size", "1920x1080_2997_longgop_standard", "FHD 29.97p"),
    ("exposure", "+0.0", "±0"), ("exposure", "-2_2/3", "−2⅔"), ("exposure", "+0_1/3", "+⅓"),
    ("av", "f6.3", "f/6.3"), ("tv", "1/125", "1/125"), ("tv", '1"', "1 s"), ("tv", '0"3', "0.3 s"),
    ("wb", "awbwhite", "AWB white"), ("af_method", "flexible_zone_2", "Flexible zone 2"), ("iso", "auto", "Auto"),
])
def test_labels(key, raw, shown):
    assert label(key, raw) == shown


def test_exposure_steps_centre_and_clamp():
    ab = ABILITY["exposure"]
    assert exposure_steps(ab, "+0.0", 5, 3) == ["+0.0", "-1.0", "+1.0", "-2.0", "+2.0"]
    assert exposure_steps(ab, "+2_2/3", 3, 3) == ["+2_2/3", "+1_2/3", "+3.0"]  # clamped at +3


def run(seq, timeout=10):
    done = []
    seq.start(on_done=done.append)
    end = time.time() + timeout
    while seq.running and time.time() < end:
        time.sleep(0.02)
    return done[0]


@pytest.fixture
def mock():
    t = MockTransport()
    t.connect()
    return t


def test_interval_takes_count_shots(mock):
    n = len(mock.files)
    assert run(Interval(mock, every=0.05, count=4)) == "Interval finished: 4 shots"
    assert len(mock.files) == n + 4


def test_interval_stops_on_request(mock):
    seq = Interval(mock, every=0.2, count=0)
    done = []
    seq.start(on_done=done.append)
    time.sleep(0.3)
    seq.stop()
    while seq.running:
        time.sleep(0.02)
    assert done == ["stopped"]


def test_bracket_restores_exposure(mock):
    mock.values["exposure"] = "+0_1/3"
    msg = run(Bracket(mock, frames=3, step_thirds=3, settle=0))
    assert "3 frames (+⅓, −⅔, +1⅓)" in msg and mock.values["exposure"] == "+0_1/3"


def test_bracket_refuses_when_exposure_locked(mock, monkeypatch):
    monkeypatch.setitem(ABILITY, "exposure", ["+0.0"])  # A+ / M: compensation fixed
    msg = run(Bracket(mock, frames=3, step_thirds=3, settle=0))
    assert "mode dial to P, Tv or Av" in msg


def test_presets_roundtrip(mock, tmp_path, monkeypatch):
    monkeypatch.setattr(sequences, "PRESETS_FILE", tmp_path / "presets.json")
    save_preset("Demo", {"movie_size": "4K Crop 59.94p", "wb": "daylight", "iso": "bogus"})
    ok, failed = apply_preset(mock, load_presets()["Demo"])
    assert ok == ["movie_size", "wb"] and failed == ["iso"]
    assert mock.values["movie_size"] == "4K Crop 59.94p"


def test_migration_from_camctl(tmp_path, monkeypatch):
    """Settings and data from the camctl name carry over once; a second run changes nothing."""
    from PySide6 import QtCore
    from studio_utility import paths

    # Native macOS preferences can persist across runs even with HOME redirected.
    # Give both the test and migrate() real QSettings backed by temporary files.
    native_qsettings = QtCore.QSettings

    def QSettings(organization, application):
        return native_qsettings(
            str(tmp_path / organization / f"{application}.ini"),
            native_qsettings.Format.IniFormat,
        )

    monkeypatch.setattr(QtCore, "QSettings", QSettings)
    monkeypatch.setattr(paths, "_OLD_DIR", tmp_path / ".camctl")
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path / ".studio-utility")
    monkeypatch.setattr(paths, "_OLD_PREFS", ("camctl-test", "camctl-test"))
    monkeypatch.setattr(paths, "PREFS", ("studio-utility-test", "Studio Utility Test"))
    (tmp_path / ".camctl" / "noise").mkdir(parents=True)
    (tmp_path / ".camctl" / "hub.json").write_text('{"token": "OLD"}')
    old = QSettings(*paths._OLD_PREFS)
    old.setValue("last_kind", "hub")
    old.sync()
    paths.migrate()
    new = QSettings(*paths.PREFS)
    assert new.value("last_kind") == "hub"
    assert (tmp_path / ".studio-utility" / "hub.json").read_text() == '{"token": "OLD"}'
    new.setValue("last_kind", "wifi")
    paths.migrate()  # already migrated: must not overwrite newer settings
    assert QSettings(*paths.PREFS).value("last_kind") == "wifi"


def test_clog3_curve_matches_canon_and_lut_exports(tmp_path):
    import numpy as np
    from studio_utility import lut
    assert round(float(lut.linear_to_clog3(0.18 / 0.9)), 3) == 0.343  # Canon: 18% grey -> 34.3%
    x = np.linspace(0, 1, 21)
    assert np.allclose(lut.linear_to_clog3(lut.clog3_to_linear(x)), x)
    table = lut.clog3_to_rec709()
    i = round(0.343 * 32)
    assert abs(float(table[i, i, i].mean()) - 0.41) < 0.02  # mid grey lands at Rec.709 mid grey
    path = lut.write_cube(table, tmp_path / "clog3.cube", "test")
    assert np.allclose(lut.load_cube(path), table, atol=1e-5)


def test_unnamed_camera_codes_read_as_other():
    from studio_utility.labels import label
    assert label("mode", "0x43") == "Other (0x43)"
