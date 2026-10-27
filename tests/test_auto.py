"""'auto' connects to the camera on this computer with no pairing: local hub if it holds the camera, else USB."""
from __future__ import annotations

import json

import pytest

from studio_utility import paths, transports
from studio_utility.transports import auto, hub


def test_local_hub_link_reads_token_from_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_DIR", tmp_path)
    (tmp_path / "hub.json").write_text(json.dumps({"token": "secret", "devices": {}}))
    assert hub.parse_link("") == ("http://localhost:8765", "secret")
    assert hub.parse_link("127.0.0.1:9000") == ("http://127.0.0.1:9000", "secret")
    assert hub.parse_link("http://localhost:8765/?token=given") == ("http://localhost:8765", "given")
    assert hub.parse_link("10.0.0.5") == ("http://10.0.0.5:8765", "")  # another computer still needs pairing


class Fake:
    def __init__(self, kind, ok, log):
        self.kind, self.ok, self.log = kind, ok, log

    def connect(self):
        self.log.append(self.kind)
        if not self.ok:
            raise RuntimeError(f"{self.kind} down")

    def disconnect(self):
        pass


@pytest.mark.parametrize("up,expected,tried", [
    ({"hub", "usb-edsdk"}, "hub", ["hub"]),  # the hub owns USB; going direct would fail with "port in use"
    ({"usb-edsdk"}, "usb-edsdk", ["hub", "usb-edsdk"]),
])
def test_auto_prefers_local_hub_then_usb(monkeypatch, up, expected, tried):
    log = []
    monkeypatch.setattr(transports, "load", lambda kind, **kw: Fake(kind, kind in up, log))
    assert auto.connect().kind == expected
    assert log == tried


def test_auto_reports_both_failures(monkeypatch):
    monkeypatch.setattr(transports, "load", lambda kind, **kw: Fake(kind, False, []))
    with pytest.raises(RuntimeError, match="local hub: hub down; USB: usb-edsdk down"):
        auto.connect()
