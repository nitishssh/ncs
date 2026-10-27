"""Headless UI tests on the mock camera (Qt offscreen)."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])  # settings are isolated by tests/conftest.py


def spin(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


@pytest.fixture
def win(app, tmp_path):
    from studio_utility.ui.app import MainWindow
    QSettings("studio-utility", "Studio Utility").clear()
    w = MainWindow()
    w.files.root = tmp_path / "imports"
    w.show()
    w.conn_btn.click()  # mock is the default
    spin(app, 0.3)
    yield w
    w.close()


def test_settings_show_labels_send_raw(win, app):
    box = win.boxes["movie_size"]
    assert box.itemText(box.findData("4K Crop 59.94p")) == "4K Crop 59.94p"
    box.setCurrentIndex(box.findData("FHD 119.88p"))
    spin(app, 0.2)
    assert win.t.values["movie_size"] == "FHD 119.88p"


def test_camera_side_changes_reach_the_ui(win, app):
    win.t.set_setting("iso", "3200")
    spin(app, 0.6)
    assert win.boxes["iso"].currentData() == "3200"


def test_overlays_render(win, app):
    for cb in (win.ov_hist, win.ov_af, win.ov_peak, win.ov_zebra):
        cb.setChecked(True)
    win.next_frame()
    assert not win.preview.pixmap().isNull()


def test_record_state_changes_only_after_camera_accepts_command(win, app, monkeypatch):
    mic_commands = []
    monkeypatch.setattr(win.audio, "camera_record", mic_commands.append)

    def reject(_start):
        raise RuntimeError("card unavailable")

    monkeypatch.setattr(win.t, "record", reject)
    win.toggle_record()
    spin(app, 0.2)

    assert win.recording is False
    assert win.rec.text() == "● Rec"
    assert mic_commands == []
    assert win.msg.text() == "Record command failed: card unavailable"


def test_slow_camera_poll_does_not_block_ui(win, monkeypatch):
    def slow_status():
        time.sleep(0.5)
        return {}

    monkeypatch.setattr(win.t, "status", slow_status)
    started = time.monotonic()
    win.refresh_status()
    assert time.monotonic() - started < 0.1


def test_slow_connection_does_not_block_ui(app, monkeypatch):
    from studio_utility import transports
    from studio_utility.ui.app import MainWindow

    real_load = transports.load

    def slow_load(*args, **kwargs):
        time.sleep(0.5)
        return real_load(*args, **kwargs)

    monkeypatch.setattr(transports, "load", slow_load)
    QSettings("studio-utility", "Studio Utility").clear()
    window = MainWindow()
    started = time.monotonic()
    window.connect_to("mock", "")
    assert time.monotonic() - started < 0.1
    assert window.conn_btn.text() == "Stop"
    spin(app, 0.8)
    assert window.t is not None and window.conn_btn.text() == "Disconnect"
    window.close()


def test_auto_import_and_import_new(win, app):
    fp = win.files
    fp.auto.setChecked(True)
    win.t.shoot()
    spin(app, 1.5)
    assert [p.name for p in fp.dest().glob("*.JPG")] == ["IMG_0004.JPG"]
    fp.import_new()
    spin(app, 2.5)
    assert sorted(p.name for p in fp.dest().glob("*.JPG")) == [f"IMG_000{i}.JPG" for i in range(1, 5)]
    fp.import_new()
    assert "already imported" in win.msg.text()


def test_reconnects_after_drop(win, app):
    old = win.t
    old.connected = False
    spin(app, 0.3)
    assert "Reconnecting" in win.msg.text() and win.conn_btn.text() == "Stop"
    spin(app, 5.5)
    assert win.t is not None and win.t is not old


def test_close_is_fast(app, tmp_path):
    from studio_utility.ui.app import MainWindow
    w = MainWindow()
    w.show()
    w.conn_btn.click()
    w.files_btn.setChecked(True)
    w.files.auto.setChecked(True)
    spin(app, 0.5)
    t0 = time.time()
    w.close()
    assert time.time() - t0 < 2


def test_builtin_log_preview_lut(win, app):
    from studio_utility.ui.app import BUILTIN_LUTS
    name = next(iter(BUILTIN_LUTS))
    win.set_lut(BUILTIN_LUTS[name](), name)
    win.next_frame()
    assert win.lut_btn.text() == f"LUT: {name}" and not win.preview.pixmap().isNull()
    win.set_lut(None, None)
    assert win.lut_btn.text() == "LUT: off"


def test_hiding_usb_settings_keeps_photo_and_movie(win, app):
    """USB lives in the More tabs: a connection without USB settings hides it there, never Photo/Movie."""
    win.caps = set(win.caps) - {"focus_drive"}
    win.apply_settings({})
    assert [win.tabs.tabText(i) for i in range(win.tabs.count()) if win.tabs.isTabVisible(i)] == ["Photo", "Movie", "More"]
    assert not win.more_tabs.isTabVisible(win.usb_tab)
