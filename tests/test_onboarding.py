"""First-run onboarding: detection picks a sensible default, the wizard returns the chosen connection."""
from __future__ import annotations

import pytest

from studio_utility.setup_checks import DEFAULT_RELAY, Found


@pytest.mark.parametrize("found,expected", [
    (Found(hubs=["http://10.11.0.2:8765/"], usb_camera="Canon camera", edsdk="x"), "auto"),  # camera is here
    (Found(hubs=["http://10.11.0.2:8765/"]), "hub"),
    (Found(usb_camera="Canon camera", edsdk="/sdk/EDSDK"), "auto"),
    (Found(usb_camera="Canon camera", gphoto=True), "usb-gphoto"),
    (Found(usb_camera="Canon camera"), "mock"),  # camera but no driver: nothing would work yet
    (Found(relay=DEFAULT_RELAY), "wifi"),
    (Found(), "mock"),
])
def test_recommendation(found, expected):
    assert found.recommended() == expected


@pytest.fixture
def qapp():
    QtWidgets = pytest.importorskip("PySide6.QtWidgets")
    import shiboken6
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    before = set(app.topLevelWidgets())
    yield app
    for w in set(app.topLevelWidgets()) - before:  # left for PySide's exit cleanup, they crash the interpreter
        shiboken6.delete(w)


def _settle(qapp, dlg, cond):
    import time
    end = time.time() + 5
    while time.time() < end and not cond():
        qapp.processEvents()
        time.sleep(0.01)


def test_wizard_usb_path(qapp):
    from studio_utility.ui.onboarding import Onboarding
    dlg = Onboarding(detect=lambda: Found(usb_camera="Canon EOS R50 V", edsdk="/sdk/EDSDK"))
    dlg.forward()  # welcome -> choose (starts detection)
    _settle(qapp, dlg, lambda: dlg.found is not None)
    assert dlg.radios["auto"].isChecked()
    dlg.forward()  # -> details
    assert "Canon EOS R50 V detected on USB" in dlg.checks.text() and "Canon SDK installed" in dlg.checks.text()
    assert not dlg.field.isVisibleTo(dlg)
    dlg.forward()  # -> phone
    dlg.forward()  # finish
    assert dlg.result() and dlg.selected() == ("auto", "")


def test_wizard_hub_path_keeps_typed_link(qapp):
    from studio_utility.ui.onboarding import Onboarding
    dlg = Onboarding(detect=lambda: Found(hubs=["http://10.11.0.2:8765/"]))
    dlg.forward()
    _settle(qapp, dlg, lambda: dlg.found is not None)
    dlg.forward()
    assert dlg.field.text() == "http://10.11.0.2:8765/"
    dlg.field.setText("http://10.11.0.2:8765/?token=abc")
    dlg.fill_details()  # a re-check must not overwrite what was typed
    assert dlg.field.text().endswith("token=abc")
    dlg.forward()
    assert dlg.qr.pixmap() is not None and not dlg.qr.pixmap().isNull()
    dlg.forward()
    assert dlg.selected() == ("hub", "http://10.11.0.2:8765/?token=abc")


def test_shown_once(qapp, monkeypatch):
    from studio_utility.ui import onboarding

    class Prefs(dict):
        def value(self, k, default=None, type=None):
            return self.get(k, default)

        def setValue(self, k, v):
            self[k] = v
    shown = []
    monkeypatch.setattr(onboarding.Onboarding, "exec", lambda self: shown.append(1) or 0)
    prefs = Prefs()
    onboarding.maybe_show(None, prefs)
    onboarding.maybe_show(None, prefs)
    assert shown == [1] and prefs["onboarded"] is True
    onboarding.maybe_show(None, prefs, force=True)  # Setup → Guided setup
    assert shown == [1, 1]
