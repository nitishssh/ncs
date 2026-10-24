"""Render studio_utility/hub/web/icon.svg into installer/icon.icns (macOS) and installer/icon.ico (Windows).
Run on a Mac: .venv/bin/python installer/make_icons.py"""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication, QImage, QPainter  # noqa: E402
from PySide6.QtSvg import QSvgRenderer  # noqa: E402

HERE = Path(__file__).parent
SVG = HERE.parent / "studio_utility" / "hub" / "web" / "icon.svg"


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    QSvgRenderer(str(SVG)).render(p)
    p.end()
    return img


def main():
    QGuiApplication(sys.argv)
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for base in (16, 32, 128, 256, 512):
            render(base).save(str(iconset / f"icon_{base}x{base}.png"))
            render(base * 2).save(str(iconset / f"icon_{base}x{base}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")], check=True)
        from PIL import Image
        big = Path(tmp) / "256.png"
        render(256).save(str(big))
        Image.open(big).save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", HERE / "icon.icns", "and", HERE / "icon.ico")


if __name__ == "__main__":
    main()
