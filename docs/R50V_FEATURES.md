# Canon EOS R50 V: features relevant to Studio Utility

Source: Canon EOS R50 V Advanced User Guide (firmware 1.2.0+), https://cam.start.canon/en/C021/manual/html/

## Connectivity (Communication Functions menu)
| Feature | What the manual says | Studio Utility use |
|---|---|---|
| **Camera Control API (CCAPI)** | Turned on in the camera at *Advanced connection → Camera Control API*. HTTP and HTTPS ports can be changed. Optional username and password. Connects through a Wi-Fi access point (setup wizard or manual entry). | Main control link. **The activation tool isn't needed.** |
| USB: choose USB connection app | *Photo Import/Remote Control* (EOS Utility, PTP), *UVC/UAC streaming*, or *Canon app(s) for iPhone* | The mode must match the transport: PTP for EDSDK and gphoto2, UVC for video |
| USB (UVC/UAC) streaming | Movie mode only. Shows `[LIVE]` on the camera. Streaming size is selectable. Audio is LPCM 16-bit, 2 channels. Nothing is recorded to the card. The camera can be **powered over USB** (USB PD, 5 V and at least 1.5 A). | `video` transport. Use USB PD power for long sessions. |
| HDMI streaming | Clean output to a capture device. Audio is LPCM 16-bit, 2 channels. | `video` transport through a capture card |
| EOS Utility over Wi-Fi | Pairing over Wi-Fi/LAN, remote live view (slower than USB) | Uses the same PTP/IP protocol as EDSDK |
| Bluetooth | Pairs with the **BR-E1/BR-E2** wireless remote (about 5 m range, works for stills and movies) and with smartphones (Camera Connect) | `ble` transport emulates a BR-E1 |
| Camera Connect / Live Switcher Mobile streaming | Streams through a phone app | Out of scope |
| image.canon upload, GPS from phone | | Possible later |

Notes: while Wi-Fi is connected, the camera's auto power off is disabled. If Wi-Fi drops during remote movie recording, recording keeps going in movie mode but stops in stills mode.

## Movie (the R50 V's strength)
- **Formats:** XF-HEVC S 4:2:2 10-bit, XF-HEVC S 4:2:0 10-bit, XF-AVC S 4:2:2 10-bit, XF-AVC S 4:2:0 8-bit (all .MP4)
- **Sizes:** 4K UHD at 29.97/25/23.98 fps; 4K Crop at 59.94/50 fps; Full HD up to 119.88 fps (slow motion, no sound)
- **Modes:** Scene Intelligent Auto, Special scenes, **Smooth skin movie**, **Movie for close-up demos** (product reviews), Movie IS, P/Tv/Av/M, Slow/Fast motion, Custom C1–C3
- **Tools:** Time-lapse, Movie self-timer, Movie auto level, Time code, Tally lamp, Zebra, False color, HDR PQ, Canon Log view assist, Anti-flicker, Movie Av in 1/8 stops, Auto slow shutter, 4K frame grab
- **Audio:** stereo built-in mic, 3.5 mm mic input, multi-function shoe mics, 4-channel recording, wind filter, attenuator, mic directionality, 48 kHz
- **Power zoom:** the RF-S 14-30mm PZ kit lens has power zoom (CCAPI v1.4 supports power zoom control)

## Stills
- 24.2 MP APS-C sensor, Dual Pixel CMOS AF, JPEG/HEIF/RAW/C-RAW, aspect ratios 3:2, 4:3, 16:9 and 1:1
- Focus bracketing, interval timer, bulb, silent shutter, electronic shutter burst
- AF: subject detection, **registering people to prioritize**, touch AF, manual focus

## Card, power and status
- Single SD slot (UHS-II). Battery information screen. Folder and file naming are configurable (CCAPI v1.4 can create folders and set file names).
