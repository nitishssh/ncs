"""Updates from the project's GitHub releases (github.com/nitishssh/ncs).

A release carries one file per platform plus SHA256SUMS.txt; the app picks its own file by name, checks it against
the checksums, and swaps itself for the new copy. The swap runs in a small helper after the app quits, because a
running app can't replace its own files (Windows locks them; on macOS the bundle is in use)."""
from __future__ import annotations

import hashlib
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

from studio_utility import __version__
from studio_utility.paths import CONFIG_DIR

LOG = CONFIG_DIR / "logs" / "update.log"  # written by the app and by the helper that swaps it


def note(line: str) -> None:
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(f"{__import__('time').strftime('%Y-%m-%d %H:%M:%S')} {line}\n")
    except OSError:
        pass

REPO = "nitishssh/ncs"
# STUDIO_UTILITY_UPDATE_URL: a test server that answers like the GitHub API (end-to-end update tests)
LATEST = os.environ.get("STUDIO_UTILITY_UPDATE_URL") or f"https://api.github.com/repos/{REPO}/releases/latest"
CHECKSUMS = "SHA256SUMS.txt"


@dataclass
class Release:
    version: str
    notes: str
    url: str          # download URL of this platform's file
    name: str         # its file name, as listed in SHA256SUMS.txt
    sums_url: str | None
    page: str


def parse_version(v: str) -> tuple[int, ...]:
    """'v0.2.0' -> (0, 2, 0); anything that isn't numbers sorts as oldest."""
    try:
        return tuple(int(p) for p in v.strip().lstrip("vV").split("-")[0].split("."))
    except ValueError:
        return ()


def is_newer(latest: str, current: str = __version__) -> bool:
    return parse_version(latest) > parse_version(current)


def asset_suffix(system: str | None = None, machine: str | None = None) -> str | None:
    """The release file this computer can install, by the end of its name."""
    system = system or sys.platform
    machine = (machine or platform.machine()).lower()
    if system == "darwin":
        return "-mac-arm64.dmg" if machine in ("arm64", "aarch64") else None  # no Intel build is published
    if system == "win32":
        return "-windows-x64.zip" if machine in ("amd64", "x86_64") else None
    return None


def pick(release_json: dict, suffix: str) -> Release | None:
    assets = {a["name"]: a["browser_download_url"] for a in release_json.get("assets", [])}
    name = next((n for n in assets if n.endswith(suffix)), None)
    if not name:
        return None
    return Release(version=release_json["tag_name"].lstrip("vV"), notes=release_json.get("body") or "",
                   url=assets[name], name=name, sums_url=assets.get(CHECKSUMS),
                   page=release_json.get("html_url", f"https://github.com/{REPO}/releases"))


def check(client: httpx.Client | None = None) -> Release | None:
    """The newer release for this computer, or None (up to date, no build for this platform)."""
    suffix = asset_suffix()
    if not suffix:
        return None
    c = client or httpx.Client(timeout=10, follow_redirects=True)
    r = c.get(LATEST, headers={"Accept": "application/vnd.github+json"})
    r.raise_for_status()
    rel = pick(r.json(), suffix)
    return rel if rel and is_newer(rel.version) else None


def expected_sha256(sums_text: str, name: str) -> str | None:
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name:
            return parts[0].lower()
    return None


def download(rel: Release, dest_dir: Path, progress: Callable[[int, int], None] | None = None,
             client: httpx.Client | None = None) -> Path:
    """Fetch the file and refuse it unless it matches the release's SHA256SUMS.txt."""
    c = client or httpx.Client(timeout=60, follow_redirects=True)
    if not rel.sums_url:
        raise RuntimeError("the release has no SHA256SUMS.txt, so the download can't be checked")
    sums = c.get(rel.sums_url)
    sums.raise_for_status()
    want = expected_sha256(sums.text, rel.name)
    if not want:
        raise RuntimeError(f"{rel.name} isn't listed in {CHECKSUMS}")
    out = dest_dir / rel.name
    h = hashlib.sha256()
    with c.stream("GET", rel.url) as r:
        r.raise_for_status()
        total, done = int(r.headers.get("content-length") or 0), 0
        with open(out, "wb") as f:
            for chunk in r.iter_bytes(1 << 20):
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress:
                    progress(done, total)
    if h.hexdigest() != want:
        out.unlink(missing_ok=True)
        raise RuntimeError("the download doesn't match its published checksum; nothing was installed")
    return out


# ---- installing (packaged app only)
def installed_location() -> Path | None:
    """What the update replaces: the .app bundle on macOS, the program folder on Windows. None when running from
    source (update with git instead) or from somewhere that can't be replaced (the read-only disk image)."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    if sys.platform == "darwin":
        app = next((p for p in exe.parents if p.suffix == ".app"), None)
        if not app or str(app).startswith("/Volumes/") or not os.access(app.parent, os.W_OK):
            return None
        return app
    if sys.platform == "win32":
        return exe.parent if os.access(exe.parent, os.W_OK) else None
    return None


def mac_script(dmg: Path, app: Path, pid: int) -> str:
    return f"""#!/bin/sh
# Studio Utility update: wait for the app to quit, swap in the new bundle, start it again
exec >>"{LOG}" 2>&1
echo "$(date '+%F %T') helper: waiting for {pid}"
while kill -0 {pid} 2>/dev/null; do sleep 0.5; done
mnt=$(mktemp -d)
hdiutil attach -nobrowse -quiet -mountpoint "$mnt" "{dmg}" || exit 1
new="$mnt/{app.name}"
if [ -d "$new" ]; then
  /usr/bin/ditto "$new" "{app}.new" && /bin/mv "{app}" "{app}.old" && /bin/mv "{app}.new" "{app}" && /bin/rm -rf "{app}.old"
fi
hdiutil detach -quiet "$mnt"
/usr/bin/xattr -dr com.apple.quarantine "{app}" 2>/dev/null
/usr/bin/open "{app}"
echo "$(date '+%F %T') helper: done"
"""


def windows_script(unpacked: Path, folder: Path, pid: int) -> str:
    """PowerShell, not a .cmd: a console-less cmd hung forever in `tasklist | find` while waiting (found
    2026-10-26), so the swap never happened. Wait-Process needs no pipe and no console."""
    exe = folder / "studio-utility.exe"
    return f"""# Studio Utility update: wait for the app to quit, copy the new files over, start it again
function Note($m) {{ Add-Content -LiteralPath '{LOG}' -Value ((Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' helper: ' + $m) }}
Note 'waiting for {pid}'
Wait-Process -Id {pid} -Timeout 300 -ErrorAction SilentlyContinue
& robocopy '{unpacked}' '{folder}' /MIR /R:10 /W:1 /NFL /NDL /NJH /NJS | Out-Null
Note "robocopy exit $LASTEXITCODE"
Start-Process -FilePath '{exe}'
Note 'done'
"""


def install_and_restart(package: Path, location: Path) -> None:
    """Start the helper that replaces `location` once this process exits. The caller then quits the app."""
    work = Path(tempfile.mkdtemp(prefix="studio-utility-update-"))
    pid = os.getpid()
    note(f"installing {package.name} over {location} (pid {pid}), helper in {work}")
    if sys.platform == "darwin":
        script = work / "update.sh"
        script.write_text(mac_script(package, location, pid))
        subprocess.Popen(["/bin/sh", str(script)], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif sys.platform == "win32":
        import zipfile
        zipfile.ZipFile(package).extractall(work / "new")
        unpacked = next((p.parent for p in (work / "new").rglob("studio-utility.exe")), None)
        if not unpacked:
            raise RuntimeError("the update has no studio-utility.exe")
        script = work / "update.ps1"
        script.write_text(windows_script(unpacked, location, pid), encoding="utf-8-sig")
        # a hidden console, not DETACHED_PROCESS: PowerShell started with no console at all exits at once
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        helper = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                   "-File", str(script)], creationflags=flags, close_fds=True,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        note(f"helper started, pid {helper.pid}")
    else:
        raise RuntimeError("updates are only installed on macOS and Windows")
