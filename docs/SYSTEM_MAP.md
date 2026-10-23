# System map (scanned 2026-10-23)

```
                 Internet (Jio mobile, IPv6 2409:40f2:…)
                          │
              Phone hotspot  10.63.196.214  (gateway + DNS)
                          │ Wi-Fi
   ┌──────────────────────┴───────────────────────┐
   │ Mac mini "Nitishs-Mac-mini"  (Apple M4, 16 GB, macOS 27.0.1)
   │   en1 Wi-Fi        10.63.196.2   ← default route / internet
   │   en0 Ethernet     (no link)
   │   bridge0 Thunderbolt Bridge  10.11.0.1
   │   USB: Owl 4K PRO camera (UVC)  ← tested OK with camctl
   │   Runs camctl (.venv, Python 3.14)
   └──────────────────────┬───────────────────────┘
                          │ Thunderbolt cable (10.11.0.0/24)
   ┌──────────────────────┴───────────────────────┐
   │ Windows PC "wednesday"  user nitish
   │   10.11.0.2 (TB)  ← reachable, ssh alias: wednesday-tb
   │   192.168.50.2 (ssh alias wednesday-cable) ← currently down
   │   wednesday.local (ssh alias wednesday-wifi)
   │   Wi-Fi 10.63.196.242  ← SAME hotspot as the Mac (default route)
   │   Tailscale 100.97.69.73, WSL 172.22.128.1
   │   Acer Predator PHN16S-71, Core Ultra 7 255HX, 15 GB, RTX 5050
   │   Windows 11 Home 10.0.26300
   │   Cameras: ACER FHD + IR (built-in)
   │   Software: py launcher, git, ffmpeg, OBS, Canon EOS Utility,
   │             EOS Network Setting Tool  (PATH `python` = hermes venv, use `py`)
   └───────────────────────────────────────────────┘

   Canon EOS R50 V: not connected yet (battery charging)
```

## Observations
- **No router right now.** Both computers get internet from the same phone hotspot (10.63.196.x). If the camera joins that hotspot too, **the Mac and the Windows PC can both reach it with no extra hardware.**
- The Thunderbolt link (10.11.0.x) is a fast private link between the two computers. Tailscale is installed on Windows.
- The Canon EOS Utility installed on Windows is useful for checking USB tethering. It's Canon's own app and uses the same SDK (EDSDK) that camctl's USB support is built on.
- **For the camera:** put the camera, the Mac (Ethernet en0, currently unused) and the Windows PC on **one router**. Then both computers can run camctl against a fixed camera IP.

## Proposed setup
```
Router (internet optional; can tether to phone)
 ├── Mac mini      ── Ethernet en0
 ├── Windows PC    ── Ethernet
 ├── Canon R50 V   ── Wi-Fi, reserved IP e.g. 192.168.50.50, CCAPI :8080
 └── other devices ── Wi-Fi / Ethernet
Mac ↔ Windows Thunderbolt link stays as a fast private side link.
```

## Current camera network (set up 2026-10-23)
Windows Mobile hotspot on "wednesday", started with `scripts/windows_hotspot.ps1`:
- SSID `camnet`, hub `192.168.137.1`, max 8 clients
- Shares internet from "Nitish's Phone" over the built-in Killer Wi-Fi 6E adapter
- Broadcast on the virtual adapter "Local Area Connection* 2". The TP-Link USB adapter ("Wi-Fi 2") is plugged in but unused.
- The camera will get `192.168.137.x`. The Mac reaches it by joining `camnet`.

## Planned setup with the new TP-Link router
```
TP-Link router (camera LAN, internet optional)
 ├── Canon R50 V ── Wi-Fi, reserved IP (e.g. 192.168.0.50), CCAPI :8080
 ├── Windows     ── USB-C Ethernet adapter (or TP-Link USB Wi-Fi)
 └── Mac (optional) ── USB-C Ethernet adapter → direct route to the camera

Mac ══ Thunderbolt bridge (10.11.0.1 ⇄ 10.11.0.2) ══ Windows
   • If the Mac is not on the router: Windows forwards 10.11.0.2:8080 → camera
     (scripts/windows_camera_proxy.ps1), camctl on the Mac uses host 10.11.0.2
   • Fast file transfer between the two computers (downloads, edits)
Phone hotspot stays for internet only; it carries no camera traffic.
```

## Working setup (verified 2026-10-23)
```
Camera R50 V ── Wi-Fi ── TL-WR845N router (192.168.0.1, LAN port, no WAN)
   192.168.0.101 / fe80::7eb8:daff:fe30:f748, CCAPI :8080 (answers on IPv6 only)
Windows "wednesday" ── USB-C Ethernet "Ethernet 3" ── router LAN (192.168.0.100, DHCP)
   runs scripts/ccapi_relay.py: 10.11.0.2:8080 → camera (firewall: Thunderbolt subnet only)
Mac ══ Thunderbolt ══ Windows   → camctl wifi host 10.11.0.2
```
Live view ~18 fps at 512×288. The camera's control API is only up while it shows "connection established".
