# PyInstaller spec for Studio Utility. Build from the project root:
#   macOS:   .venv/bin/pyinstaller --noconfirm installer/studio-utility.spec   -> dist/Studio Utility.app
#   Windows: .venv\Scripts\pyinstaller --noconfirm installer\studio-utility.spec -> dist\studio-utility\studio-utility.exe
import sys

from PyInstaller.utils.hooks import collect_submodules

root = SPECPATH + "/.."
# Transports are imported by name at runtime (studio_utility.transports.load), so list them explicitly
hidden = collect_submodules("studio_utility.transports") + ["studio_utility.ui.app"]
# Large Qt parts Studio Utility doesn't use
excludes = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtPdf",
            "PySide6.QtPdfWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "tkinter", "pytest"]

a = Analysis([root + "/installer/launch.py"], pathex=[root], hiddenimports=hidden, excludes=excludes)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="studio-utility", console=False,
          argv_emulation=sys.platform == "darwin")
coll = COLLECT(exe, a.binaries, a.datas, name="studio-utility")

if sys.platform == "darwin":
    app = BUNDLE(coll, name="Studio Utility.app", bundle_identifier="com.studio-utility.app", info_plist={
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # Reaching the camera relay on the Thunderbolt network needs macOS local-network permission
        "NSLocalNetworkUsageDescription": "Studio Utility connects to your Canon camera on the local network.",
        "NSCameraUsageDescription": "Studio Utility shows the camera's USB webcam (UVC) feed.",
        "NSBluetoothAlwaysUsageDescription": "Studio Utility can act as a Bluetooth remote for your Canon camera.",
    })
