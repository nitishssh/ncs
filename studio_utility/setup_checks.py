"""What can this computer reach right now? Used by first-run onboarding. Read-only: nothing here changes the
camera or opens a camera session that would block another app."""
from __future__ import annotations

import socket
import subprocess
import sys
from dataclasses import dataclass, field

DEFAULT_RELAY = "10.11.0.2:8090"  # the Windows relay on the Thunderbolt link
HUB_SERVICE = "_studio-utility._tcp.local."


@dataclass
class Found:
    usb_camera: str | None = None        # e.g. "Canon EOS R50 V" when a Canon is on this computer's USB
    edsdk: str | None = None             # path to Canon's SDK library
    gphoto: bool = False                 # python-gphoto2 importable
    hubs: list[str] = field(default_factory=list)   # http://host:port/ of Studio Hubs on the network
    relay: str | None = None             # camera relay / Camera Control API address that answered

    def recommended(self) -> str:
        if self.hubs:
            return "hub"
        if self.usb_camera and (self.edsdk or self.gphoto):
            return "usb-edsdk" if self.edsdk else "usb-gphoto"
        if self.relay:
            return "wifi"
        return "mock"


def usb_canon() -> str | None:
    """Name of a Canon camera on USB, without opening it (the OS device list only)."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["ioreg", "-p", "IOUSB", "-w0"], capture_output=True, text=True, timeout=5).stdout
            return "Canon camera" if "Canon" in out else None
        if sys.platform == "win32":
            ps = "Get-PnpDevice -PresentOnly | ? FriendlyName -match 'Canon|EOS' | select -first 1 -expand FriendlyName"
            out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                 timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
            return out or None
        out = subprocess.run(["lsusb"], capture_output=True, text=True, timeout=5).stdout
        return "Canon camera" if "Canon" in out else None
    except Exception:
        return None


def edsdk_path() -> str | None:
    from studio_utility.edsdk_native import find_library
    p = find_library()
    return str(p) if p and p.exists() else None


def gphoto_available() -> bool:
    try:
        import gphoto2  # noqa: F401
        return True
    except Exception:
        return False


def reachable(address: str, timeout: float = 0.6) -> bool:
    host, _, port = address.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def find_hubs(seconds: float = 2.0) -> list[str]:
    """Studio Hubs announcing themselves over Bonjour."""
    try:
        from zeroconf import ServiceBrowser, Zeroconf
    except ImportError:
        return []
    import time
    found: list[str] = []

    class Listener:
        def add_service(self, zc, type_, name):
            info = zc.get_service_info(type_, name, timeout=1500)
            if info and info.parsed_addresses():
                # local network first; Tailscale (100.64/10) works too but is slower
                ipv4 = sorted((a for a in info.parsed_addresses() if ":" not in a), key=lambda a: a.startswith("100."))
                found.append(f"http://{(ipv4 or info.parsed_addresses())[0]}:{info.port}/")

        def update_service(self, *a):
            pass

        def remove_service(self, *a):
            pass

    zc = Zeroconf()
    try:
        ServiceBrowser(zc, HUB_SERVICE, Listener())
        time.sleep(seconds)
    finally:
        zc.close()
    return sorted(set(found))


def detect() -> Found:
    return Found(usb_camera=usb_canon(), edsdk=edsdk_path(), gphoto=gphoto_available(), hubs=find_hubs(),
                 relay=DEFAULT_RELAY if reachable(DEFAULT_RELAY) else None)
