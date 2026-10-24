"""CLI: `studio-utility` opens the UI; subcommands for scripting."""
import argparse
import json


def main():
    p = argparse.ArgumentParser(prog="studio-utility")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("ui")
    s = sub.add_parser("shoot")
    s.add_argument("--via", default="wifi", help="wifi | ble | usb-edsdk | usb-gphoto | video")
    s.add_argument("--host", default="192.168.1.2")
    g = sub.add_parser("get")
    g.add_argument("key")
    g.add_argument("--host", default="192.168.1.2")
    d = sub.add_parser("edsdk-discover", help="list every camera property reachable over USB (EDSDK)")
    d.add_argument("--json", action="store_true")
    b = sub.add_parser("ble-scan")
    b.add_argument("--services", action="store_true", help="connect to first camera and dump GATT")
    a = p.parse_args()

    from studio_utility import transports
    if a.cmd in (None, "ui"):
        from studio_utility.ui.app import run
        run()
    elif a.cmd == "shoot":
        t = transports.load(a.via, **({"host": a.host} if a.via == "wifi" else {}))
        t.connect()
        t.shoot()
        t.disconnect()
    elif a.cmd == "get":
        t = transports.load("wifi", host=a.host)
        t.connect()
        print(json.dumps(t.get_setting(a.key), indent=2))
    elif a.cmd == "edsdk-discover":
        t = transports.load("usb-edsdk")
        t.connect()
        try:
            print(t.info().model)
            found = t.discover()
        finally:
            t.disconnect()
        if a.json:
            print(json.dumps(found, indent=2))
        else:
            for p in found:
                print(f"{p['id']}  {p['name']:18} options={p['options']:<3} value={p['value']}")
        print(f"{len(found)} properties. Paste this output back so the remote controls can be extended.")
    elif a.cmd == "ble-scan":
        from studio_utility.transports.ble import BLETransport
        found = BLETransport.scan()
        print(found or "No Canon cameras found")
        if a.services and found:
            t = BLETransport(found[0][0])
            t.connect()
            print("\n".join(t.services()))
            t.disconnect()


if __name__ == "__main__":
    main()
