"""Forward the camera's CCAPI port to the Mac over the Thunderbolt link.

Runs on the Windows PC (which is on the camera's router). Works with IPv4 and with the camera's
link-local IPv6 address, which Windows' built-in `netsh portproxy` cannot forward.

    .venv\\Scripts\\python scripts\\ccapi_relay.py --camera fe80::7eb8:daff:fe30:f748 --iface "Ethernet 3"
    .venv\\Scripts\\python scripts\\ccapi_relay.py --camera 192.168.0.101

Then on the Mac: camctl -> wifi -> 10.11.0.2
"""
from __future__ import annotations

import argparse
import asyncio
import ipaddress
import socket
import subprocess
import time


_index_cache: dict[str, tuple[float, int]] = {}


def iface_index(name: str) -> int:
    """Windows interface index for a link-local IPv6 scope, e.g. 'Ethernet 3' -> 12.
    Cached 30 s: looking it up starts PowerShell (~0.5 s), but it changes if the adapter is re-plugged."""
    hit = _index_cache.get(name)
    if hit and time.time() - hit[0] < 30:
        return hit[1]
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"(Get-NetAdapter -Name '{name}').ifIndex"],
                         capture_output=True, text=True, check=True).stdout.strip()
    _index_cache[name] = (time.time(), int(out))
    return int(out)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, asyncio.CancelledError):
        pass
    finally:
        writer.close()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--camera", required=True, help="camera IPv4 or IPv6 address")
    ap.add_argument("--iface", default="Ethernet 3", help="adapter on the camera's network (IPv6 link-local only)")
    ap.add_argument("--port", type=int, default=8080, help="camera's CCAPI port")
    ap.add_argument("--listen-port", type=int, default=None, help="port the Mac connects to (default: same as --port)")
    ap.add_argument("--listen", default="10.11.0.2", help="Thunderbolt address of this PC")
    ap.add_argument("--allow", default="10.11.0.0/24", help="only these clients may connect")
    a = ap.parse_args()

    cam = ipaddress.ip_address(a.camera)
    allowed = ipaddress.ip_network(a.allow)
    if cam.version == 6 and cam.is_link_local:
        # Resolved per connection: the USB Ethernet adapter's index changes if it's re-plugged
        target = lambda: (str(cam), a.port, 0, iface_index(a.iface))
        family = socket.AF_INET6
    else:
        target = lambda: (str(cam), a.port)
        family = socket.AF_INET6 if cam.version == 6 else socket.AF_INET

    async def handle(cr: asyncio.StreamReader, cw: asyncio.StreamWriter) -> None:
        peer = cw.get_extra_info("peername")[0]
        if ipaddress.ip_address(peer) not in allowed:
            cw.close()
            return
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.setblocking(False)
        try:
            await asyncio.wait_for(asyncio.get_running_loop().sock_connect(sock, target()), 5)
            tr, tw = await asyncio.open_connection(sock=sock)
        except Exception as e:
            print(f"camera unreachable: {e}")
            sock.close()
            cw.close()
            return
        await asyncio.gather(pipe(cr, tw), pipe(tr, cw))

    async def serve() -> None:
        server = await asyncio.start_server(handle, a.listen, a.listen_port or a.port)
        print(f"Relaying {a.listen}:{a.listen_port or a.port} -> camera {a.camera} (clients {a.allow}). Ctrl+C to stop.")
        async with server:
            await server.serve_forever()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        pass
    except OSError as e:
        if getattr(e, "winerror", None) == 10048 or e.errno in (48, 98, 10048):
            print(f"Port {a.listen_port or a.port} on {a.listen} is already in use (another relay or program).")
            return
        raise


if __name__ == "__main__":
    main()
