"""'auto': the camera on this computer with no address or pairing. If the local hub already holds the camera
(USB allows one owner), go through it; otherwise drive the camera over USB directly."""
from __future__ import annotations

from studio_utility import transports


def connect(hub_first: bool = True):
    """Return a connected transport: local hub when it has the camera, else USB (EDSDK)."""
    errors = []
    order = ("hub", "usb-edsdk") if hub_first else ("usb-edsdk", "hub")
    for kind in order:
        t = transports.load(kind)
        try:
            t.connect()
            return t
        except Exception as error:  # noqa: BLE001 - try the next way in, report all at the end
            errors.append(f"{'local hub' if kind == 'hub' else 'USB'}: {error}")
            try:
                t.disconnect()
            except Exception:  # noqa: BLE001
                pass
    raise RuntimeError("no camera on this computer (" + "; ".join(errors) + ")")
