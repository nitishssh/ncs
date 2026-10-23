"""python -m camctl.hub [--camera 10.11.0.2:8090 | --camera mock] [--port 8765]"""
from __future__ import annotations

import argparse
import socket

from aiohttp import web

from camctl.hub.server import Hub, lan_addresses

SERVICE = "_camctl._tcp.local."


def advertise(port: int):
    """Bonjour/mDNS: camctl apps on the network find the hub without typing an address."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        return None
    addrs = [socket.inet_aton(a) for a in lan_addresses()]
    if not addrs:
        return None
    name = socket.gethostname().split(".")[0]
    info = ServiceInfo(SERVICE, f"camctl hub on {name}.{SERVICE}", addresses=addrs, port=port,
                       properties={"path": "/", "version": "1"}, server=f"{name}.local.")
    try:
        zc = Zeroconf()
        # A previous run's announcement may still be cached on the network: take "… (2)" instead of failing
        zc.register_service(info, allow_name_change=True)
        return zc, info
    except Exception as e:  # discovery is a convenience; the hub must start regardless
        print(f"  (Bonjour unavailable: {e!r}; devices can still use the address or QR code)")
        return None


def main():
    ap = argparse.ArgumentParser(prog="camctl.hub", description=__doc__)
    ap.add_argument("--camera", default="10.11.0.2:8090", help="host:port of the camera's CCAPI, or 'mock'")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.camera == "mock":
        hub = Hub("mock")
    else:
        host, _, port = a.camera.partition(":")
        hub = Hub("wifi", host=host, port=int(port or 8080))
    zc = advertise(a.port)
    print(f"camctl hub: http://localhost:{a.port}/pair shows the pairing QR code")
    for addr in lan_addresses():
        print(f"  reachable at http://{addr}:{a.port}/")
    try:
        web.run_app(hub.app(), port=a.port, print=None)
    finally:
        if zc:
            zc[0].unregister_service(zc[1])
            zc[0].close()


if __name__ == "__main__":
    main()
