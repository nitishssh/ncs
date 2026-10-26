# Canon EDSDK: your to-do list

Studio Utility can now control the camera over **USB with Canon's EDSDK** (the protocol EOS Utility uses).
The code is written and tested against a fake SDK; it needs Canon's real library to run, and one test
session with the camera to confirm it works and to see what extra controls (e.g. Canon Log) the R50 V
exposes.

## 1. Get the SDK (only you can do this)
1. Register (free) at the **Canon Developer Community** for your region. For India: Canon Asia's developer
   resources page → *Camera* → *EOS Digital SDK (EDSDK)*.
2. Accept the EDSDK licence and download the **Windows** package (also the **macOS** one if you want USB on
   the Mac).
3. In the release notes, check that **EOS R50 V** is in the supported-camera list. If it isn't, take the
   newest version available and tell me which version you got.

> Don't share the SDK files publicly or add them to git: Canon's licence doesn't allow redistribution.
> The `vendor/` folder is already git-ignored.

## 2. Put it where Studio Utility looks
- **Windows (for the hub):** unzip into `C:\Users\Nitish\studio-utility\vendor\EDSDK\`
  (any sub-folders are fine; it finds `EDSDK.dll` and prefers the 64-bit one).
- **Mac (optional):** unzip into `/Volumes/NIT-SSD/Development/ncs/vendor/EDSDK/` (it finds
  `EDSDK.framework`).
- Or set `STUDIO_UTILITY_EDSDK` to the full path of `EDSDK.dll` / `EDSDK.framework/EDSDK`.

## 3. Prepare the camera
1. **Close EOS Utility** on the PC (it takes the camera for itself).
2. Camera menu → **Network → Choose USB connection app → Photo Import/Remote Control**.
3. Connect the camera to the **Windows PC** with a USB-C **data** cable.
4. Turn the camera on (Wi-Fi Camera Control API can be left off; USB takes over).

## 4. Run the discovery and send me the output
From the Mac:
```bash
ssh wednesday-tb "cd studio-utility && .venv\Scripts\studio-utility edsdk-discover"
```
It prints every property the camera answers over USB. Paste the output back: it tells us whether Canon Log,
the movie format and other C3 settings can be changed remotely, and I'll add controls for whatever is there.

## 5. Turn on USB for the hub
```bash
ssh wednesday-tb "powershell -NoProfile -ExecutionPolicy Bypass -File studio-utility\scripts\windows_hub_autostart.ps1"
```
The hub then uses USB when the camera is plugged in and the SDK is present, and Wi-Fi otherwise.
Your phone and Mac need no changes.

## What's built (and what isn't verified yet)
| | Built | Verified with the real SDK |
|---|---|---|
| Connect, model, firmware, battery, shots left | ✅ | ✅ Mac, 2026-10-26 |
| ISO, shutter, aperture, exposure comp., WB, picture style, shooting mode | ✅ | ✅ Mac, 2026-10-26 |
| Shoot, AF (half-press), movie record start/stop | ✅ | ✅ Mac, 2026-10-26 |
| Live view over USB | ✅ | ✅ Mac, 2026-10-26 |
| Card file list, download with progress/cancel, thumbnails | ✅ | ✅ Mac, 2026-10-26 |
| Camera change events → all devices update | ✅ | ⏳ |
| Property discovery (`edsdk-discover`, `/api/discover`) | ✅ | ⏳ |
| Live view in S&F (slow/fast motion) mode | — | ❌ camera sends no frames (0xA104); use C1/movie modes |
| Canon Log 3 / movie format / movie size over USB | ❓ depends on step 4 | ⏳ |
| Power zoom, tap-to-focus over USB | ❌ Wi-Fi only for now | — |
