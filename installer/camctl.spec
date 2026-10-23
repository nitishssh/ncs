# PyInstaller spec for camctl. Build from the project root:
#   macOS:   .venv/bin/pyinstaller --noconfirm installer/camctl.spec   -> dist/camctl.app
#   Windows: .venv\Scripts\pyinstaller --noconfirm installer\camctl.spec -> dist\camctl\camctl.exe
import sys

from PyInstaller.utils.hooks import collect_submodules

root = SPECPATH + "/.."
# Transports are imported by name at runtime (camctl.transports.load), so list them explicitly
hidden = collect_submodules("camctl.transports") + ["camctl.ui.app"]
# Large Qt parts camctl doesn't use
excludes = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtPdf",
            "PySide6.QtPdfWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "tkinter", "pytest"]

a = Analysis([root + "/installer/launch.py"], pathex=[root], hiddenimports=hidden, excludes=excludes)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="camctl", console=False,
          argv_emulation=sys.platform == "darwin")
coll = COLLECT(exe, a.binaries, a.datas, name="camctl")

if sys.platform == "darwin":
    app = BUNDLE(coll, name="camctl.app", bundle_identifier="com.camctl.app", info_plist={
        "CFBundleShortVersionString": "0.1.0",
        "NSHighResolutionCapable": True,
        # Reaching the camera relay on the Thunderbolt network needs macOS local-network permission
        "NSLocalNetworkUsageDescription": "camctl connects to your Canon camera on the local network.",
        "NSCameraUsageDescription": "camctl shows the camera's USB webcam (UVC) feed.",
        "NSBluetoothAlwaysUsageDescription": "camctl can act as a Bluetooth remote for your Canon camera.",
    })
