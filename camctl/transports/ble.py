"""Bluetooth LE remote (shutter / record / wake) using bleak. macOS + Windows.

Canon's BLE protocol is not publicly documented. The UUIDs and byte values below
come from community reverse-engineering of the BR-E1 remote and MUST be verified
against your R50 V (use `camctl ble-scan --services`). Edit them here if they differ.
"""
from __future__ import annotations

import asyncio

from bleak import BleakClient, BleakScanner

from camctl.core import CameraInfo, NotSupported, Transport

PAIR_CHAR = "00050002-0000-1000-0000-d8492fffa821"     # unverified
REMOTE_CHAR = "00050003-0000-1000-0000-d8492fffa821"   # unverified
DEVICE_NAME = b"camctl"
SHUTTER_PRESS, SHUTTER_RELEASE = b"\x0c", b"\x00"      # unverified
RECORD_TOGGLE = b"\x10"                                # unverified


class BLETransport(Transport):
    name = "ble"

    def __init__(self, address: str | None = None):
        self.address = address
        self.client: BleakClient | None = None
        self.loop = asyncio.new_event_loop()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    @staticmethod
    def scan(timeout: float = 8.0) -> list[tuple[str, str]]:
        devices = asyncio.run(BleakScanner.discover(timeout=timeout))
        return [(d.address, d.name or "") for d in devices if d.name and "EOS" in d.name.upper()]

    def connect(self) -> None:
        if not self.address:
            found = self.scan()
            if not found:
                raise RuntimeError("No Canon camera advertising over BLE. Enable Bluetooth pairing on the camera.")
            self.address = found[0][0]
        self.client = BleakClient(self.address)
        self._run(self.client.connect())
        self._run(self.client.write_gatt_char(PAIR_CHAR, b"\x03" + DEVICE_NAME, response=True))

    def disconnect(self) -> None:
        if self.client:
            self._run(self.client.disconnect())
            self.client = None

    def services(self) -> list[str]:
        return [f"{s.uuid}: {[c.uuid for c in s.characteristics]}" for s in self.client.services]

    def info(self) -> CameraInfo:
        return CameraInfo(model="Canon (BLE)", extra={"address": self.address})

    def shoot(self, autofocus: bool = True) -> None:
        async def press():
            await self.client.write_gatt_char(REMOTE_CHAR, SHUTTER_PRESS, response=False)
            await asyncio.sleep(0.15)
            await self.client.write_gatt_char(REMOTE_CHAR, SHUTTER_RELEASE, response=False)
        self._run(press())

    def record(self, start: bool) -> None:
        self._run(self.client.write_gatt_char(REMOTE_CHAR, RECORD_TOGGLE, response=False))

    def get_setting(self, key: str) -> dict:
        raise NotSupported("BLE remote cannot read settings; use Wi-Fi or USB")
