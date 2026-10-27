# Studio Utility

Custom control software for the Canon EOS R50 V on macOS and Windows. It uses the best tool for each link:

| Link | Module | Built on |
|---|---|---|
| Wi-Fi | `transports/ccapi.py` | Canon CCAPI (HTTP/JSON) |
| Bluetooth | `transports/ble.py` | bleak (shutter, record, wake) |
| USB | `transports/edsdk.py` | Canon EDSDK (macOS and Windows) |
| USB fallback | `transports/gphoto.py` | libgphoto2 (macOS) |
| Video | `transports/uvc.py` | OpenCV (UVC webcam mode or HDMI capture) |

## Setup
```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/studio-utility            # opens the UI
```

## Per-link notes
- **Wi-Fi:** on the R50 V, turn on Camera Control API in the camera menu (Network → Advanced connection → Camera Control API; no Canon tool needed) and connect it to the router. The camera answers on IPv6 only, so the Mac reaches it through `scripts/ccapi_relay.py` on the Windows PC: choose **wifi** and `10.11.0.2:8090`. See `docs/SYSTEM_MAP.md`.
- **USB (EDSDK):** download EDSDK from the Canon Developer portal and set `STUDIO_UTILITY_EDSDK` to `EDSDK.dll` or `EDSDK.framework/EDSDK`.
- **USB (gphoto2, macOS):** run `brew install gphoto2 && pip install -e .[gphoto]`, then `killall PTPCamera` before connecting.
- **Bluetooth:** the UUIDs in `ble.py` come from community reverse-engineering and are not verified. Run `studio-utility ble-scan --services` and adjust them to match your camera.
- **Video:** set the camera to USB streaming (UVC) or use an HDMI capture dongle. The field takes the device index (0, 1, …).

## Dolby exports
Files panel → **Dolby ▾**, or `studio-utility dolby vision|eac3|ac3 CLIP...`. Each export is written next to the original clip.
- **Dolby Vision 8.1** (`<clip>_dv.mp4`): shoot with **HDR PQ** on (Canon Log 3 and SDR need grading first). Needs `brew install x265 dovi_tool gpac`. The picture is re-encoded, which is slow.
- **Dolby Digital Plus / Dolby Digital** (`<clip>_eac3.mp4`, `<clip>_ac3.mp4`): the video is copied untouched and the audio is re-encoded. Needs only ffmpeg.

## Packaging
Use PyInstaller to build `Studio Utility.app` on macOS and `studio-utility.exe` on Windows.

## Tests
```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m pytest -q
```
`tests/fake_r50v.py` replays replies recorded from the real R50 V (fw 1.2.0), so the camera's quirks
(battery as a word, paged file lists, `0"3` shutter values, busy live view frames, the 2+1 connection
limit…) stay fixed without the camera connected.
