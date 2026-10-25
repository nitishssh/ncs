"""Canon EDSDK property IDs and value codes (from EDSDKTypes.h), mapped to the same raw values the CCAPI
transport uses ("1/125", "f4.0", "+0_1/3", "daylight"…), so labels, presets and the web app work unchanged.

Verify against your SDK version's EDSDKTypes.h; unknown codes are shown as hex rather than guessed."""
from __future__ import annotations

# ---- property IDs
PROP = {
    "product_name": 0x00000002,
    "firmware": 0x00000007,
    "battery": 0x00000008,
    "save_to": 0x0000000B,
    "image_quality": 0x00000100,
    "wb": 0x00000106,
    "color_temperature": 0x00000107,
    "color_space": 0x0000010D,
    "picture_style": 0x00000114,
    "mode": 0x00000400,          # AE mode (P/Tv/Av/M/C1-3/Auto...)
    "drive": 0x00000401,
    "iso": 0x00000402,
    "metering": 0x00000403,
    "af_mode": 0x00000404,
    "av": 0x00000405,
    "tv": 0x00000406,
    "exposure": 0x00000407,
    "shots_left": 0x0000040A,
    "evf_output": 0x00000500,    # live view output device: bit 1 = TFT, bit 2 = PC
    "evf_mode": 0x00000501,
    "evf_af_mode": 0x0000050E,
    "record": 0x00000510,        # movie: 4 = start, 0 = stop
}

# ---- commands
CMD_TAKE_PICTURE = 0x00000000
CMD_EXTEND_SHUTDOWN = 0x00000001
CMD_PRESS_SHUTTER = 0x00000004
CMD_DO_EVF_AF = 0x00000102
CMD_DRIVE_LENS_EVF = 0x00000103
SHUTTER_OFF, SHUTTER_HALF, SHUTTER_FULL = 0, 1, 3
EVF_OUTPUT_PC = 2
RECORD_START, RECORD_STOP = 4, 0
SAVE_TO_CAMERA = 1

# ---- events
PROPERTY_EVENT_ALL = 0x00000100
PROPERTY_EVENT_CHANGED = 0x00000101
PROPERTY_EVENT_DESC_CHANGED = 0x00000102
OBJECT_EVENT_ALL = 0x00000200
OBJECT_EVENT_DIR_ITEM_CREATED = 0x00000204
OBJECT_EVENT_DIR_ITEM_REQUEST_TRANSFER = 0x00000208
STATE_EVENT_ALL = 0x00000300
STATE_EVENT_SHUTDOWN = 0x00000301

# ---- errors
ERR = {
    0x00000000: "OK", 0x00000002: "unimplemented", 0x00000003: "internal error", 0x00000005: "not supported",
    0x00000060: "communication port in use (close EOS Utility)", 0x00000081: "device busy",
    0x000000C0: "camera not found", 0x0000A102: "object not ready (live view starting)",
    0x00008D01: "take picture: AF failed", 0x00008D07: "take picture: card error",
    0x00008D08: "take picture: no card", 0x0000A105: "session not open",
}
ERR_DEVICE_BUSY, ERR_OBJECT_NOTREADY, ERR_NOT_SUPPORTED = 0x81, 0xA102, 0x07

# ---- value tables: EDSDK code <-> CCAPI-style raw value
ISO = {0x00: "auto", 0x40: "50", 0x48: "100", 0x4B: "125", 0x4D: "160", 0x50: "200", 0x53: "250", 0x55: "320",
       0x58: "400", 0x5B: "500", 0x5D: "640", 0x60: "800", 0x63: "1000", 0x65: "1250", 0x68: "1600",
       0x6B: "2000", 0x6D: "2500", 0x70: "3200", 0x73: "4000", 0x75: "5000", 0x78: "6400", 0x7B: "8000",
       0x7D: "10000", 0x80: "12800", 0x83: "16000", 0x85: "20000", 0x88: "25600", 0x8B: "32000",
       0x8D: "40000", 0x90: "51200"}

AV = {0x08: "f1.0", 0x0B: "f1.1", 0x0C: "f1.2", 0x0D: "f1.2", 0x10: "f1.4", 0x13: "f1.6", 0x14: "f1.8",
      0x15: "f1.8", 0x18: "f2.0", 0x1B: "f2.2", 0x1C: "f2.5", 0x1D: "f2.5", 0x20: "f2.8", 0x23: "f3.2",
      0x24: "f3.5", 0x25: "f3.5", 0x28: "f4.0", 0x2B: "f4.5", 0x2C: "f4.5", 0x2D: "f5.0", 0x30: "f5.6",
      0x33: "f6.3", 0x34: "f6.7", 0x35: "f7.1", 0x38: "f8.0", 0x3B: "f9.0", 0x3C: "f9.5", 0x3D: "f10",
      0x40: "f11", 0x43: "f13", 0x44: "f13", 0x45: "f14", 0x48: "f16", 0x4B: "f18", 0x4C: "f19", 0x4D: "f20",
      0x50: "f22", 0x53: "f25", 0x54: "f27", 0x55: "f29", 0x58: "f32"}

TV = {0x0C: "bulb", 0x10: '30"', 0x13: '25"', 0x14: '20"', 0x15: '20"', 0x18: '15"', 0x1B: '13"', 0x1C: '10"',
      0x1D: '10"', 0x20: '8"', 0x23: '6"', 0x24: '6"', 0x25: '5"', 0x28: '4"', 0x2B: '3"2', 0x2C: '3"',
      0x2D: '2"5', 0x30: '2"', 0x33: '1"6', 0x34: '1"5', 0x35: '1"3', 0x38: '1"', 0x3B: '0"8', 0x3C: '0"7',
      0x3D: '0"6', 0x40: '0"5', 0x43: '0"4', 0x44: '0"3', 0x45: '0"3', 0x48: "1/4", 0x4B: "1/5", 0x4C: "1/6",
      0x4D: "1/6", 0x50: "1/8", 0x53: "1/10", 0x54: "1/10", 0x55: "1/13", 0x58: "1/15", 0x5B: "1/20",
      0x5C: "1/20", 0x5D: "1/25", 0x60: "1/30", 0x63: "1/40", 0x64: "1/45", 0x65: "1/50", 0x68: "1/60",
      0x6B: "1/80", 0x6C: "1/90", 0x6D: "1/100", 0x70: "1/125", 0x73: "1/160", 0x74: "1/180", 0x75: "1/200",
      0x78: "1/250", 0x7B: "1/320", 0x7C: "1/350", 0x7D: "1/400", 0x80: "1/500", 0x83: "1/640", 0x84: "1/750",
      0x85: "1/800", 0x88: "1/1000", 0x8B: "1/1250", 0x8C: "1/1500", 0x8D: "1/1600", 0x90: "1/2000",
      0x93: "1/2500", 0x94: "1/3000", 0x95: "1/3200", 0x98: "1/4000", 0x9B: "1/5000", 0x9C: "1/6000",
      0x9D: "1/6400", 0xA0: "1/8000"}

EXPOSURE = {0x18: "+3.0", 0x15: "+2_2/3", 0x14: "+2_1/2", 0x13: "+2_1/3", 0x10: "+2.0", 0x0D: "+1_2/3",
            0x0C: "+1_1/2", 0x0B: "+1_1/3", 0x08: "+1.0", 0x05: "+0_2/3", 0x04: "+0_1/2", 0x03: "+0_1/3",
            0x00: "+0.0", 0xFD: "-0_1/3", 0xFC: "-0_1/2", 0xFB: "-0_2/3", 0xF8: "-1.0", 0xF5: "-1_1/3",
            0xF4: "-1_1/2", 0xF3: "-1_2/3", 0xF0: "-2.0", 0xED: "-2_1/3", 0xEC: "-2_1/2", 0xEB: "-2_2/3",
            0xE8: "-3.0"}

WB = {0: "auto", 1: "daylight", 2: "cloudy", 3: "tungsten", 4: "fluorescent", 5: "flash", 6: "custom",
      8: "shade", 9: "colortemp", 23: "awbwhite"}

PICTURE_STYLE = {0x81: "standard", 0x82: "portrait", 0x83: "landscape", 0x84: "neutral", 0x85: "faithful",
                 0x86: "monochrome", 0x87: "auto", 0x88: "finedetail", 0x21: "userdef1", 0x22: "userdef2",
                 0x23: "userdef3"}

MODE = {0: "P", 1: "Tv", 2: "Av", 3: "M", 4: "bulb", 7: "C1", 16: "C2", 17: "C3", 9: "auto",
        19: "flexible", 20: "movie", 22: "scene_auto", 0xFFFFFFFF: "unknown"}

TABLES = {"iso": ISO, "av": AV, "tv": TV, "exposure": EXPOSURE, "wb": WB, "picture_style": PICTURE_STYLE,
          "mode": MODE}


def to_value(key: str, code: int) -> str:
    table = TABLES.get(key, {})
    return table.get(code, f"0x{code:X}")


def to_code(key: str, value: str) -> int:
    """Raw value ("1/125", or "0x70" for codes we have no name for) -> EDSDK code."""
    if value.startswith("0x"):
        return int(value, 16)
    for code, v in TABLES.get(key, {}).items():
        if v == value:
            return code
    raise ValueError(f"unknown {key} value {value!r}")


def battery_percent(raw: int) -> int | None:
    """kEdsPropID_BatteryLevel: 0-100, or 0xFFFFFFFF when on AC/USB power."""
    return None if raw in (0xFFFFFFFF, -1) or raw > 100 else int(raw)


def error_text(code: int) -> str:
    return ERR.get(code, f"EDSDK error 0x{code:08X}")
