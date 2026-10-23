# Studio Utility roadmap: closing the gap with the best camera software

Based on the comparison with EOS Utility, Camera Connect, digiCamControl, qDslrDashboard,
Capture One / Lightroom and Smart Shooter (2026-10-23). Every item is built against the mock
first, then verified on the real R50 V through the Windows relay.

## ✅ Phase 1: Monitoring tools (quick wins, about 1 day)
| # | Item | How | Done when |
|---|---|---|---|
| 1.1 | **Histogram overlay** | Parse `liveviewdata.histogram` from `flipdetail?kind=info` (the camera already sends it) and draw a luma/RGB graph over the preview | Graph updates live; matches the camera's own histogram |
| 1.2 | **AF frame overlay** | Draw `afframe` boxes from the same info stream; green when focused | Box follows the subject in whole-area AF |
| 1.3 | **Focus peaking** | OpenCV edge mask (Laplacian) tinted on the preview, toggle button | Sharp edges glow; off by default |
| 1.4 | **Zebras / false color** | Threshold on the preview frame (e.g. 95%+ striped), toggle | Overexposed areas striped |
| 1.5 | **Pretty setting labels** | Map raw values (`3840x2160_2500_longgop_standard`, `-0_1/3`) to readable labels (`4K 25p`, `−⅓`) | No raw API strings in the UI |

## ✅ Phase 2: Files workflow (1–2 days)
| # | Item | How | Done when |
|---|---|---|---|
| 2.1 | **Download progress + cancel** | Stream in a worker thread with a progress bar; shows MB/s and time left | A 1 GB clip shows progress and the UI stays responsive |
| 2.2 | **Thumbnail grid** | CCAPI `?kind=thumbnail` per file, lazy-loaded, photos and movies in tabs | Grid of the card; scroll is smooth with 200+ files |
| 2.3 | **Auto-download new shots** | Event polling (`ver110/event/polling`) for added files; optional JPEG-only | A new shot appears on the Mac within a few seconds |
| 2.4 | **Ratings** | Star rating written to the card (CCAPI rating) | Rating visible on the camera's playback |
| 2.5 | **Import folder + naming** | Choose destination, date subfolders, keep originals byte-identical | Files land in `~/Pictures/R50V/2026-10-23/` |

## ✅ Phase 3: Shooting tools (focus stacking not yet) (2–3 days)
| # | Item | How | Done when |
|---|---|---|---|
| 3.1 | **Event-driven status** | Replace the 1 s status polling with long-poll events (battery, card, settings, recording) | Setting changed on the camera shows in the app within 1 s |
| 3.2 | **Interval / time-lapse** | Interval, count, duration; uses shutter (photo) or rec (movie) | 100-shot interval completes unattended |
| 3.3 | **Bracketing helper** | Steps exposure compensation around a shot | 3/5/7-frame bracket on card |
| 3.4 | **Focus stacking helper** | Uses the camera's focus bracketing if available, else manual focus steps | Stack of N frames with shifting focus |
| 3.5 | **Presets** | Save/restore named groups of settings (e.g. "Product demo 4K") | One click applies 5+ settings |

## Phase 4: Reliability and polish (ongoing)
| # | Item | How | Done when |
|---|---|---|---|
| 4.1 | ✅ **Auto-reconnect** | Detect a dropped camera or relay, retry with backoff, keep the UI state | Turning the camera off and on reconnects by itself |
| 4.2 | ✅ **Relay as a Windows service** | Start `ccapi_relay.py` at login (Task Scheduler), find the camera's IPv6 automatically | No manual relay command needed |
| 4.3 | **Connection wizard** | One screen: mock / Wi-Fi via relay / Wi-Fi direct / USB / webcam, with guided checks | A new user connects without reading docs |
| 4.4 | **Packaging** | PyInstaller `.app` and `.exe` | Double-click to run on both machines |
| 4.5 | ✅ **Automated tests** (41) | Mock-based UI tests plus a recorded-response CCAPI test for the R50 V | Tests run before every change |

## Phase 5: Untested connections
| # | Item | Done when |
|---|---|---|
| 5.1 | USB webcam (UVC) with the R50 V | Live view in Studio Utility and OBS, delay measured |
| 5.2 | USB tethering (EDSDK) | Shoot + download over USB on Windows (EDSDK download needed) |
| 5.3 | Bluetooth remote | Shutter/record over BLE; UUIDs verified with `studio-utility ble-scan --services` |

## Later, if needed
- Multi-camera control (a second CCAPI camera).
- Web UI served by the relay (any phone or browser on the network; CORS is supported by CCAPI 1.2+).
- 5 GHz router to cut Wi-Fi delay (the TL-WR845N is 2.4 GHz only).

## Suggested order
Phase 1 first: it is small, visible, and uses data the camera already sends.
Then 2.1 and 2.2 (files), then 4.1 and 4.2 (so daily use needs no manual steps).
