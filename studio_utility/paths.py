"""Where Studio Utility keeps its data, and the one-time move from the old "camctl" names."""
from __future__ import annotations

import shutil
from pathlib import Path

PREFS = ("studio-utility", "Studio Utility")    # QSettings organization, application
CONFIG_DIR = Path.home() / ".studio-utility"   # presets, noise profiles, voice settings, hub token
_OLD_DIR = Path.home() / ".camctl"
_OLD_PREFS = ("camctl", "camctl")


def migrate() -> None:
    """Carry data over from camctl (the project's earlier name). Safe to call on every start."""
    if _OLD_DIR.is_dir() and not CONFIG_DIR.exists():
        shutil.copytree(_OLD_DIR, CONFIG_DIR)
    try:
        from PySide6.QtCore import QSettings
    except ImportError:  # the hub runs without Qt
        return
    new, old = QSettings(*PREFS), QSettings(*_OLD_PREFS)
    for store in (new, old):  # on macOS, don't count system-wide defaults (AppleLocale…) as our keys
        store.setFallbacksEnabled(False)
    if not new.allKeys() and old.allKeys():
        for key in old.allKeys():
            new.setValue(key, old.value(key))
        new.sync()
