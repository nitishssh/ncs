"""First-run onboarding: welcome, find the camera, set up the chosen connection, pair a phone. Runs on first
launch and again from Setup. Returns the connection the main window should use."""
from __future__ import annotations

import io
import threading

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QButtonGroup, QDialog, QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton,
                               QStackedWidget, QVBoxLayout, QWidget)

from studio_utility import setup_checks

STYLE = """
QDialog { background:#121212; }
QLabel { color:#e8e8e8; }
QLabel#title { font-size:24px; font-weight:600; }
QLabel#sub { color:#9a9a9a; font-size:13px; }
QLabel#check { font-size:13px; padding:2px 0; }
QRadioButton { color:#e8e8e8; font-size:14px; padding:10px 12px; border:1px solid #2e2e2e; border-radius:10px; }
QRadioButton:checked { border-color:#e8e8e8; background:#1c1c1c; }
QRadioButton:disabled { color:#555; }
QLineEdit { background:#1e1e1e; color:#e8e8e8; border:1px solid #333; border-radius:6px; padding:7px 9px; }
QPushButton { background:#2a2a2a; color:#e8e8e8; border:1px solid #3a3a3a; border-radius:8px; padding:8px 18px; }
QPushButton#primary { background:#e8e8e8; color:#111; font-weight:600; }
"""

CHOICES = [  # key, title, what it means
    ("hub", "Studio Hub", "Share the camera with your phone, Mac and PC. Best when the camera stays on one computer."),
    ("usb-edsdk", "USB cable", "Plug the camera into this computer. Most controls, fastest file transfer."),
    ("usb-gphoto", "USB cable (open-source driver)", "USB without Canon's SDK. Mac only; fewer controls."),
    ("wifi", "Wi-Fi", "The camera's Camera Control API over your router. No cable."),
    ("mock", "Try the demo", "A simulated camera, to look around first."),
]


def _ok(flag: bool, yes: str, no: str) -> str:
    return f"{'✓' if flag else '•'}  {yes if flag else no}"


class Onboarding(QDialog):
    detected = Signal(object)

    def __init__(self, parent=None, detect=setup_checks.detect):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Studio Utility")
        self.setMinimumSize(560, 520)
        self.setStyleSheet(STYLE)
        self.found: setup_checks.Found | None = None
        self.kind, self.value = "mock", ""
        self._field_kind = None
        self._detect = detect
        self.pages = QStackedWidget()
        for build in (self.page_welcome, self.page_choose, self.page_details, self.page_phone):
            self.pages.addWidget(build())
        self.back = QPushButton("Back")
        self.next = QPushButton("Get started")
        self.next.setObjectName("primary")
        self.back.clicked.connect(lambda: self.go(self.pages.currentIndex() - 1))
        self.next.clicked.connect(self.forward)
        nav = QHBoxLayout()
        nav.addWidget(self.back)
        nav.addStretch()
        nav.addWidget(self.next)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 28, 28, 20)
        root.addWidget(self.pages, 1)
        root.addLayout(nav)
        self.detected.connect(self.show_found)
        self.go(0)

    # ---- pages
    @staticmethod
    def _page(title: str, sub: str) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        t, s = QLabel(title), QLabel(sub)
        t.setObjectName("title")
        s.setObjectName("sub")
        s.setWordWrap(True)
        lay.addWidget(t)
        lay.addWidget(s)
        lay.addSpacing(8)
        return w, lay

    def page_welcome(self):
        w, lay = self._page("Studio Utility", "Control your Canon from this computer and your phone.")
        for line in ("Live view with a Canon Log 3 preview while you record",
                     "Every setting the camera allows remotely, over Wi-Fi or USB",
                     "Files straight off the card, voice cleaned up and synced into clips"):
            lab = QLabel("—  " + line)
            lab.setObjectName("check")
            lay.addWidget(lab)
        lay.addStretch()
        return w

    def page_choose(self):
        w, lay = self._page("How is your camera connected?", "Looking for your camera…")
        self.choose_sub = lay.itemAt(1).widget()
        self.group = QButtonGroup(self)
        self.radios: dict[str, QRadioButton] = {}
        for key, title, about in CHOICES:
            r = QRadioButton(f"{title}\n{about}")
            self.group.addButton(r)
            self.radios[key] = r
            lay.addWidget(r)
        self.radios["mock"].setChecked(True)
        lay.addStretch()
        return w

    def page_details(self):
        w, lay = self._page("", "")
        self.details_title = lay.itemAt(0).widget()
        self.details_sub = lay.itemAt(1).widget()
        self.checks = QLabel()
        self.checks.setObjectName("check")
        self.checks.setWordWrap(True)
        self.field = QLineEdit()
        self.recheck = QPushButton("Check again")
        self.recheck.clicked.connect(self.start_detect)
        lay.addWidget(self.field)
        lay.addWidget(self.checks)
        lay.addWidget(self.recheck, 0, Qt.AlignLeft)
        lay.addStretch()
        return w

    def page_phone(self):
        w, lay = self._page("Control from your phone", "")
        self.phone_sub = lay.itemAt(1).widget()
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignCenter)
        lay.addWidget(self.qr)
        lay.addStretch()
        return w

    # ---- flow
    def go(self, i: int):
        i = max(0, min(i, self.pages.count() - 1))
        self.pages.setCurrentIndex(i)
        self.back.setVisible(i > 0)
        self.next.setText(["Get started", "Continue", "Continue", "Finish"][i])
        if i == 1 and self.found is None:
            self.start_detect()
        if i == 2:
            self.fill_details()
        if i == 3:
            self.fill_phone()

    def forward(self):
        i = self.pages.currentIndex()
        if i == 1:
            self.kind = next(k for k, r in self.radios.items() if r.isChecked())
        if i == 2:
            self.value = self.field.text().strip() if self.kind in ("hub", "wifi") else ""
        if i == 3 or (i == 2 and self.kind == "mock"):
            self.accept()
            return
        self.go(i + 1)

    def start_detect(self):
        self.choose_sub.setText("Looking for your camera…")
        self.checks.setText("Checking…")
        threading.Thread(target=lambda: self.detected.emit(self._detect()), daemon=True).start()

    def show_found(self, found: setup_checks.Found):
        self.found = found
        bits = []
        if found.usb_camera:
            bits.append(f"{found.usb_camera} on USB")
        if found.hubs:
            bits.append(f"Studio Hub at {found.hubs[0]}")
        if found.relay:
            bits.append(f"camera relay at {found.relay}")
        self.choose_sub.setText(("Found: " + ", ".join(bits) + ".") if bits else
                                "Nothing found yet. Pick how you'll connect; the next step shows what to do.")
        self.radios["usb-gphoto"].setVisible(found.gphoto)
        self.radios[found.recommended()].setChecked(True)
        if self.pages.currentIndex() == 2:
            self.fill_details()

    def fill_details(self):
        f = self.found or setup_checks.Found()
        k = self.kind
        title, sub, field, checks = "", "", None, []
        if k == "hub":
            title = "Pair with Studio Hub"
            sub = "On the computer running the hub, open http://localhost:8765/pair and copy the pairing link here."
            field = f.hubs[0] if f.hubs else "http://"
            checks = [_ok(bool(f.hubs), f"Hub found at {f.hubs[0] if f.hubs else ''}",
                          "No hub announced itself yet; paste its link anyway")]
        elif k == "wifi":
            title, sub = "Camera over Wi-Fi", ("Camera menu → Network → Camera Control API, connected to your router. "
                                               "Enter the camera's address, or the relay's.")
            field = f.relay or setup_checks.DEFAULT_RELAY
            checks = [_ok(bool(f.relay), f"{f.relay} answers", "Address not answering yet")]
        elif k in ("usb-edsdk", "usb-gphoto"):
            title = "Camera on USB"
            sub = ("Use a data cable, set the camera's USB connection app to Photo Import/Remote Control, and turn its "
                   "Wi-Fi off — the camera won't do both.")
            driver = (_ok(bool(f.edsdk), "Canon SDK installed", "Canon SDK missing: put it in vendor/EDSDK (see docs)")
                      if k == "usb-edsdk" else _ok(f.gphoto, "gphoto2 installed", "pip install gphoto2"))
            checks = [_ok(bool(f.usb_camera), f"{f.usb_camera or 'Camera'} detected on USB",
                          "No Canon on USB yet: cable in, camera on"), driver,
                      "•  Close EOS Utility (and Photos / Image Capture on a Mac)"]
        else:
            title, sub = "Demo camera", "Everything works with a simulated camera. Connect the real one any time from Setup."
        self.details_title.setText(title)
        self.details_sub.setText(sub)
        self.field.setVisible(field is not None)
        if field is not None and self._field_kind != k:  # prefill once per choice; keep what the user typed
            self.field.setText(field)
            self._field_kind = k
        self.checks.setText("\n".join(checks))
        self.recheck.setVisible(k != "mock")
        self.next.setText("Finish" if k == "mock" else "Continue")

    def fill_phone(self):
        link = self.value if self.kind == "hub" and "token=" in self.value else ""
        if link:
            self.phone_sub.setText("Scan with your phone's camera, or open the Studio Utility app on Android and scan.")
            try:
                import qrcode
                buf = io.BytesIO()
                qrcode.make(link, box_size=7, border=2).save(buf, format="PNG")
                pix = QPixmap()
                pix.loadFromData(buf.getvalue())
                self.qr.setPixmap(pix)
            except Exception:
                self.qr.setText(link)
        else:
            self.qr.clear()
            self.phone_sub.setText("Run Studio Hub on the computer the camera is connected to, then open its pairing "
                                   "page (http://<that computer>:8765/pair) and scan the code with your phone. "
                                   "You can skip this now.")

    def selected(self) -> tuple[str, str]:
        return self.kind, self.value


def maybe_show(parent, prefs, force: bool = False) -> tuple[str, str] | None:
    """First launch (or Setup): run onboarding, remember it ran, return the chosen connection."""
    if not force and prefs.value("onboarded", False, type=bool):
        return None
    dlg = Onboarding(parent)
    accepted = dlg.exec()
    prefs.setValue("onboarded", True)
    return dlg.selected() if accepted else None
