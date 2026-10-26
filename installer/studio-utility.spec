# PyInstaller spec for Studio Utility. Build from the project root:
#   macOS:   .venv/bin/pyinstaller --noconfirm installer/studio-utility.spec   -> dist/Studio Utility.app
#   Windows: .venv\Scripts\pyinstaller --noconfirm installer\studio-utility.spec -> dist\studio-utility\studio-utility.exe
import sys

from PyInstaller.utils.hooks import collect_all, collect_submodules

root = SPECPATH + "/.."
# Transports are imported by name at runtime (studio_utility.transports.load), so list them explicitly
hidden = collect_submodules("studio_utility.transports") + ["studio_utility.ui.app", "studio_utility.ui.onboarding"]
# Large Qt parts Studio Utility doesn't use
excludes = ["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtPdf",
            "PySide6.QtPdfWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick", "PySide6.QtQml",
            "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtMultimedia", "tkinter", "pytest"]

# Native pieces that are found at runtime, not by import analysis: ffmpeg binary, DSP/plugin host, mDNS
datas, binaries = [], []
for pkg in ("imageio_ffmpeg", "pedalboard", "noisereduce", "zeroconf", "soundfile", "sounddevice", "qrcode", "gphoto2"):
    try:
        d, b, h = collect_all(pkg)
    except Exception:  # optional (gphoto2 has no Windows wheels)
        continue
    datas += d
    binaries += b
    hidden += h

icon = root + ("/installer/icon.icns" if sys.platform == "darwin" else "/installer/icon.ico")
a = Analysis([root + "/installer/launch.py"], pathex=[root], hiddenimports=hidden, excludes=excludes,
             datas=datas, binaries=binaries)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="studio-utility", console=False,
          argv_emulation=sys.platform == "darwin", icon=icon)
coll = COLLECT(exe, a.binaries, a.datas, name="studio-utility")

if sys.platform == "darwin":
    app = BUNDLE(coll, name="Studio Utility.app", bundle_identifier="com.studio-utility.app", icon=icon, info_plist={
        "CFBundleShortVersionString": "0.2.0",
        "NSHighResolutionCapable": True,
        # Reaching the camera relay on the Thunderbolt network needs macOS local-network permission
        "NSLocalNetworkUsageDescription": "Studio Utility connects to your Canon camera on the local network.",
        "NSCameraUsageDescription": "Studio Utility shows the camera's USB webcam (UVC) feed.",
        "NSBluetoothAlwaysUsageDescription": "Studio Utility can act as a Bluetooth remote for your Canon camera.",
        # External mics (DJI Mic, Kreo Sonik): without this macOS silently blocks audio input
        "NSMicrophoneUsageDescription": "Studio Utility records your external microphones (DJI Mic, Kreo Sonik).",
    })
