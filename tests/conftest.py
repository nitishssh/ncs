"""Run every test in a throwaway home folder: the app's settings (macOS preferences / QSettings),
presets, noise profiles and hub token must never touch the real user's."""
import os
import tempfile

_home = tempfile.mkdtemp(prefix="studio-utility-tests-")
os.environ["HOME"] = _home          # read by Path.home() and by macOS CFPreferences
os.environ["USERPROFILE"] = _home   # Windows
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
