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
