"""Headless UI tests on the mock camera (Qt offscreen)."""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from studio_utility import paths  # noqa: E402


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
    QSettings(*paths.PREFS).clear()
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
    QSettings(*paths.PREFS).clear()
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


def test_camera_audio_settings_in_audio_tab(win, app):
    assert win.audio.camera_box.isVisibleTo(win.audio)
    box = win.boxes["wind_filter"]
    box.setCurrentIndex(box.findData("off"))
    spin(app, 0.2)
    assert win.t.values["wind_filter"] == "off"


def test_audio_editor_opens_trims_and_exports(win, app, tmp_path):
    import numpy as np
    import soundfile as sf
    path = tmp_path / "2026-10-26_143005_MicA_X.wav"
    sf.write(path, 0.2 * np.sin(np.arange(48000 * 2) / 10)[:, None], 48000, subtype="PCM_24")
    win.audio.open_editor(path)
    ed = win.audio.editor
    spin(app, 0.3)
    assert ed.isVisible() and ed.take.duration == 2.0 and "not cleaned yet" in ed.info.text()
    ed.take.set_trim(0.5, 1.5)
    ed.show_state()
    assert ed.reset_btn.isEnabled() and "keeping" in ed.info.text()
    ed.wave.grab()  # paints raw + selection without errors
    ed.export(False)
    assert (tmp_path / "2026-10-26_143005_MicA_X_edit.wav").exists()
    ed.close()


def test_tests_never_touch_the_real_app_settings():
    from studio_utility.ui import app as app_module
    assert paths.PREFS != ("studio-utility", "Studio Utility") and app_module.PREFS == paths.PREFS


def test_quitting_does_not_crash(tmp_path):
    """Smoke test of the start-up/quit path in a fresh interpreter. The real crash (SIGSEGV in ~QPushButton at exit)
    only showed up after camera + Files use and does not reproduce headless; this guards finish() itself."""
    import subprocess
    import sys
    code = (
        "import os, sys\n"
        "from studio_utility import paths; paths.PREFS = ('studio-utility-test', 'Studio Utility Test')\n"
        "from PySide6.QtCore import QTimer\n"
        "from PySide6.QtWidgets import QApplication\n"
        "from studio_utility.ui import app as m\n"
        "app = QApplication([])\n"
        "win = m.MainWindow(); win.show()\n"
        "win.audio.open_editor(None) if False else None\n"
        "QTimer.singleShot(500, win.close); QTimer.singleShot(700, app.quit)\n"
        "sys.exit(m.finish(app, win, app.exec()))\n")
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen", "HOME": str(tmp_path)}
    p = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr[-2000:]


def test_rec_button_never_stays_disabled_when_a_command_is_pending(win, app, monkeypatch):
    monkeypatch.setattr(win.io, "submit", lambda *a, **k: False)  # an earlier Rec still in flight
    win.toggle_record()
    assert win.rec.isEnabled() and "still answering" in win.msg.text()


def test_reconnect_backs_off_instead_of_hammering_the_camera():
    from studio_utility.ui.app import retry_delay
    assert [retry_delay(n) for n in range(0, 8)] == [5000, 5000, 10000, 20000, 40000, 60000, 60000, 60000]


def test_card_list_is_newest_first_across_photos_and_movies():
    """Seen on the R50 V: every photo was listed before a clip shot a minute earlier."""
    from studio_utility.ui.files import newest_first
    paths = ["edsdk:/DCIM/100CANON/IMG_0145.JPG", "edsdk:/DCIM/100CANON/IMG_0146.JPG",
             "edsdk:/XFVC/A_0001C049A261027_013258E6_CANON.MP4", "edsdk:/XFVC/A_0001C051A261027_0159138U_CANON.MP4"]
    by_name_only = newest_first(paths)
    assert by_name_only[0].endswith("C051A261027_0159138U_CANON.MP4")  # movie names carry the time
    times = {paths[0]: 1000.0, paths[1]: 9e9, paths[2]: 2000.0, paths[3]: 3000.0}
    assert newest_first(paths, times)[0].endswith("IMG_0146.JPG")    # transport times win


def test_files_panel_fetches_only_the_first_thumbnails(app, monkeypatch):
    """259 thumbnails fetched back to back froze the window over USB: fetch the newest few, the rest on scroll."""
    from studio_utility.ui import files
    asked = []

    class Loader:
        def __init__(self, t, paths, busy=None):
            asked.extend(paths)
            self.paths = list(paths)
            self.loaded = type("S", (), {"connect": lambda *_: None})()

        def start(self):
            pass

        def isRunning(self):
            return True

    monkeypatch.setattr(files, "ThumbLoader", Loader)
    panel = files.FilesPanel()
    panel.t = type("T", (), {"list_files": lambda self: [f"edsdk:/DCIM/IMG_{n:04}.JPG" for n in range(259)]})()
    panel.caps = {"thumbnail"}
    panel.refresh()
    assert len(asked) == files.FIRST_THUMBS and asked[0].endswith("IMG_0258.JPG")


def test_movie_tab_explains_when_the_link_has_no_movie_settings(win, app):
    win.tabs.setCurrentIndex(1)  # Photo is the first tab
    win.apply_settings({})  # e.g. EDSDK on the R50 V: no movie properties
    assert win.movie_note.isVisibleTo(win)
    win.load_settings()
    spin(app, 0.3)
    assert not win.movie_note.isVisibleTo(win)  # the mock camera has them


def test_auto_stays_auto_after_connecting(app, monkeypatch):
    """auto hands back a hub or EDSDK transport; the saved choice must stay "auto", not that transport's name."""
    from studio_utility import transports
    from studio_utility.ui.app import MainWindow

    def fake_auto(**kwargs):
        t = transports.load("mock")
        t.connect()
        t.name = "hub"
        return t

    real_load = transports.load
    monkeypatch.setattr(transports, "load", lambda kind, **kw: fake_auto(**kw) if kind == "auto" else real_load(kind, **kw))
    QSettings(*paths.PREFS).clear()
    window = MainWindow()
    window.connect_to("auto", "")
    spin(app, 0.5)
    assert window.t is not None
    assert QSettings(*paths.PREFS).value("last_kind") == "auto"
    window.close()
