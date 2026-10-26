"""Updates from GitHub releases: version compare, picking this platform's file, refusing a bad download."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import httpx
import pytest

from studio_utility import __version__, updater

ROOT = Path(__file__).resolve().parent.parent


def test_one_version_everywhere():
    """0.1.1 shipped with three different numbers; the updater compares against __version__."""
    pyproject = re.search(r'^version = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    bundle = re.search(r'"CFBundleShortVersionString": "([^"]+)"', (ROOT / "installer/studio-utility.spec").read_text()).group(1)
    android = re.search(r'versionName "([^"]+)"', (ROOT / "android/app/build.gradle").read_text()).group(1)
    assert pyproject == bundle == android == __version__


@pytest.mark.parametrize("latest,current,newer", [
    ("v0.2.1", "0.2.0", True), ("0.10.0", "0.9.9", True), ("v0.2.0", "0.2.0", False),
    ("v0.1.9", "0.2.0", False), ("nightly", "0.2.0", False),
])
def test_is_newer(latest, current, newer):
    assert updater.is_newer(latest, current) is newer


@pytest.mark.parametrize("system,machine,suffix", [
    ("darwin", "arm64", "-mac-arm64.dmg"), ("darwin", "x86_64", None),
    ("win32", "AMD64", "-windows-x64.zip"), ("linux", "x86_64", None),
])
def test_asset_suffix(system, machine, suffix):
    assert updater.asset_suffix(system, machine) == suffix


RELEASE = {"tag_name": "v9.0.0", "body": "notes", "html_url": "https://github.com/nitishssh/ncs/releases/tag/v9.0.0",
           "assets": [{"name": n, "browser_download_url": f"https://dl/{n}"} for n in
                      ("SHA256SUMS.txt", "StudioUtility-9.0.0-mac-arm64.dmg", "StudioUtility-9.0.0-windows-x64.zip",
                       "StudioUtility-9.0.0-android.apk")]}


def test_pick_and_check(monkeypatch):
    rel = updater.pick(RELEASE, "-windows-x64.zip")
    assert rel.version == "9.0.0" and rel.name.endswith(".zip") and rel.sums_url == "https://dl/SHA256SUMS.txt"
    monkeypatch.setattr(updater, "asset_suffix", lambda: "-mac-arm64.dmg")
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=RELEASE)))
    assert updater.check(client).name == "StudioUtility-9.0.0-mac-arm64.dmg"
    old = dict(RELEASE, tag_name="v0.0.1")
    client = httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200, json=old)))
    assert updater.check(client) is None


def _serve(payload: bytes, listed_hash: str):
    def handler(req):
        if req.url.path.endswith("SHA256SUMS.txt"):
            return httpx.Response(200, text=f"{listed_hash}  StudioUtility-9.0.0-mac-arm64.dmg\n")
        return httpx.Response(200, content=payload)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_download_checks_sha256(tmp_path):
    rel = updater.pick(RELEASE, "-mac-arm64.dmg")
    good = b"new app" * 1000
    out = updater.download(rel, tmp_path, client=_serve(good, hashlib.sha256(good).hexdigest()))
    assert out.read_bytes() == good
    with pytest.raises(RuntimeError, match="checksum"):
        updater.download(rel, tmp_path, client=_serve(b"tampered", hashlib.sha256(good).hexdigest()))
    assert not (tmp_path / rel.name).exists()  # nothing half-installed is left behind


def test_download_refuses_without_checksums(tmp_path):
    rel = updater.pick(dict(RELEASE, assets=RELEASE["assets"][1:]), "-mac-arm64.dmg")
    with pytest.raises(RuntimeError, match="SHA256SUMS"):
        updater.download(rel, tmp_path, client=_serve(b"x", "0" * 64))


def test_not_installed_when_running_from_source():
    assert updater.installed_location() is None  # pytest isn't a frozen app


def test_helper_scripts_wait_for_the_app_then_relaunch(tmp_path):
    mac = updater.mac_script(tmp_path / "u.dmg", Path("/Applications/Studio Utility.app"), 4242)
    assert "kill -0 4242" in mac and '/usr/bin/open "/Applications/Studio Utility.app"' in mac
    win = updater.windows_script(tmp_path / "new", Path(r"C:\Users\x\AppData\Local\Programs\Studio Utility"), 4242)
    assert "Wait-Process -Id 4242" in win and "robocopy" in win and "studio-utility.exe" in win
    assert "| find" not in win  # hung forever in a console-less cmd
