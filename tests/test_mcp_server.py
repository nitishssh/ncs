"""MCP server with the mock camera: protocol, the read/act split, no delete tool, and over the hub transport."""
from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys

import pytest

from studio_utility.core import CameraLost, NotSupported
from studio_utility.mcp_server import ACT_TOOLS, CameraTools, respond, serve
from studio_utility.transports.hub import HubTransport
from studio_utility.transports.mock import MockTransport
from tests.test_hub import hub  # noqa: F401 - the fixture


@pytest.fixture
def camera():
    camera = MockTransport()
    camera.connect()
    return camera


@pytest.fixture
def tools(camera, tmp_path):
    return CameraTools(lambda: camera, tmp_path / "downloads")


def rpc(tools, method, params=None, id=1):
    return respond(tools, json.dumps({"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}).encode())


def call(tools, name, **args):
    return rpc(tools, "tools/call", {"name": name, "arguments": args})["result"]


def data(result):
    assert not result.get("isError"), result
    return json.loads(result["content"][0]["text"])


def test_initialize_negotiates_protocol(tools):
    assert rpc(tools, "initialize", {"protocolVersion": "2025-03-26"})["result"]["protocolVersion"] == "2025-03-26"
    newest = rpc(tools, "initialize", {"protocolVersion": "1999-01-01"})["result"]
    assert newest["protocolVersion"] == "2025-06-18" and "tools" in newest["capabilities"]


def test_protocol_errors_and_notifications(tools):
    assert rpc(tools, "nonsense")["error"]["code"] == -32601
    assert rpc(tools, "tools/call", {"name": "act_format_card"})["error"]["code"] == -32602
    assert rpc(tools, "tools/call", {"name": "close"})["error"]["code"] == -32602  # not every method is a tool
    assert respond(tools, b"{broken")["error"]["code"] == -32700
    assert respond(tools, b'{"jsonrpc":"2.0","method":"notifications/initialized"}') is None


def test_malformed_requests_get_an_error_and_the_server_survives(tools):
    for params in ([1], {"name": ["x"]}):
        assert rpc(tools, "tools/call", params)["error"]["code"] == -32603
    assert rpc(tools, "ping")["result"] == {}


def test_serve_answers_line_by_line(tools):
    out = io.BytesIO()
    serve(tools, io.BytesIO(b'{"jsonrpc":"2.0","method":"notifications/initialized"}\n\n'
                            b'{"jsonrpc":"2.0","id":7,"method":"ping"}\n'), out)
    assert json.loads(out.getvalue()) == {"jsonrpc": "2.0", "id": 7, "result": {}}


def test_tool_names_say_what_they_do_to_the_camera(tools):
    listed = rpc(tools, "tools/list")["result"]["tools"]
    for tool in listed:
        reads = tool["name"].startswith("read_")
        assert tool["annotations"]["readOnlyHint"] is reads
        if tool["name"].startswith("act_"):
            assert tool["description"].startswith("CHANGES THE CAMERA")
    assert {t["name"] for t in ACT_TOOLS} <= {t["name"] for t in listed}


def test_no_tool_can_delete_card_contents(tools):
    for tool in rpc(tools, "tools/list")["result"]["tools"]:
        assert not any(word in tool["name"] for word in ("delete", "erase", "format", "remove", "wipe"))


def test_read_only_mode_hides_and_refuses_actions(camera, tmp_path):
    tools = CameraTools(lambda: camera, tmp_path, read_only=True)
    names = {t["name"] for t in rpc(tools, "tools/list")["result"]["tools"]}
    assert not any(n.startswith("act_") for n in names) and "read_status" in names
    assert rpc(tools, "tools/call", {"name": "act_shoot_photo"})["error"]["code"] == -32602
    assert len(camera.files) == 3


def test_read_tools_leave_the_camera_alone(tools, camera):
    before = dict(camera.values), list(camera.files)
    status = data(call(tools, "read_status"))
    assert status["connected_via"] == "mock" and "mock" in status["model"] and status["status"]["battery"] > 0
    settings = data(call(tools, "read_settings", keys=["iso", "no_such_key"]))
    assert settings["settings"]["iso"]["value"] == "400" and "no_such_key" in settings["unavailable"]
    frame = call(tools, "read_liveview_frame")["content"][0]
    assert frame["mimeType"] == "image/jpeg" and base64.b64decode(frame["data"])[:2] == b"\xff\xd8"
    listing = data(call(tools, "read_file_list", limit=2))
    assert listing["total"] == 3 and listing["files"] == camera.files[-2:]
    assert call(tools, "read_thumbnail", path=camera.files[0])["content"][0]["type"] == "image"
    assert before == (camera.values, camera.files)


def test_shoot_then_new_files(tools, camera):
    assert data(call(tools, "act_shoot_photo")) == {"accepted": True}
    assert data(call(tools, "read_new_files", timeout=3))["new_files"] == [camera.files[-1]]
    assert data(call(tools, "read_new_files", timeout=0))["new_files"] == []


def test_new_files_keeps_waiting_past_other_events(tools, camera):
    replies = iter([[], [], ["/card/IMG_9.JPG"]])
    camera.wait_new_files = lambda timeout: next(replies)
    assert data(call(tools, "read_new_files", timeout=5))["new_files"] == ["/card/IMG_9.JPG"]


def test_recording_start_stop_and_refuses_double_start(tools, camera):
    assert data(call(tools, "act_start_recording")) == {"recording": True}
    assert camera.recording_since is not None
    again = call(tools, "act_start_recording")
    assert again["isError"] and "already recording" in again["content"][0]["text"]
    assert data(call(tools, "act_stop_recording")) == {"recording": False}
    assert camera.recording_since is None


def test_set_setting_reports_what_the_camera_has_afterwards(tools, camera):
    assert data(call(tools, "act_set_setting", key="iso", value="800"))["applied"] is True
    assert camera.values["iso"] == "800"
    refused = call(tools, "act_set_setting", key="iso", value="7")
    assert refused["isError"] and "not a valid" in refused["content"][0]["text"]
    camera.set_setting = lambda key, value: None  # accepted, then ignored
    ignored = data(call(tools, "act_set_setting", key="iso", value="1600"))
    assert ignored["applied"] is False and ignored["value"] == "800"


def test_focus_and_zoom(tools, camera):
    assert not call(tools, "act_autofocus").get("isError")
    assert not call(tools, "act_focus_at", x=0.25, y=0.75).get("isError")
    assert camera.af_point == (0.25, 0.75)
    assert not call(tools, "act_zoom", position=100).get("isError")
    assert camera.zoom_pos == 100


def test_unsupported_action_is_explained(tools):
    result = call(tools, "act_focus_nudge", step=1)  # the mock has no manual focus drive
    assert result["isError"] and "Not supported" in result["content"][0]["text"]


def test_download_saves_only_card_files_and_never_overwrites(tools, camera, tmp_path):
    remote = camera.files[0]
    saved = data(call(tools, "download_file", path=remote))
    assert saved["saved_to"] == str(tmp_path / "downloads" / "IMG_0001.JPG") and saved["bytes"] > 0
    second = call(tools, "download_file", path=remote)
    assert second["isError"] and "already exists" in second["content"][0]["text"]
    outside = call(tools, "download_file", path="../../etc/passwd")
    assert outside["isError"] and "not on the card" in outside["content"][0]["text"]


def test_wrong_arguments_are_a_tool_error(tools):
    assert call(tools, "act_zoom")["isError"]
    assert call(tools, "read_status", surprise=1)["isError"]


def test_connects_lazily_and_reports_failure_then_recovers(camera, tmp_path):
    attempts = []

    def connect():
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("no camera on this computer")
        return camera
    tools = CameraTools(connect, tmp_path)
    rpc(tools, "tools/list")
    assert not attempts
    failed = call(tools, "read_status")
    assert failed["isError"] and "no camera" in failed["content"][0]["text"]
    assert data(call(tools, "read_status"))["connected_via"] == "mock"


def test_lost_camera_reconnects_but_never_repeats_the_action(camera, tmp_path):
    connects, shots = [], []

    def lost(autofocus=True):
        shots.append(1)
        raise CameraLost("unplugged")
    camera.shoot = lost
    tools = CameraTools(lambda: connects.append(1) or camera, tmp_path)
    result = call(tools, "act_shoot_photo")
    assert result["isError"] and "may or may not" in result["content"][0]["text"]
    assert shots == [1] and tools.camera is None
    call(tools, "read_status")
    assert len(connects) == 2


def test_release_camera_disconnects(tools, camera):
    call(tools, "read_status")
    assert data(call(tools, "release_camera")) == {"released": True}
    assert camera.connected is False


def test_not_supported_keeps_the_connection(tools, camera):
    def refuse(position):
        raise NotSupported("zoom")
    camera.zoom = refuse
    assert call(tools, "act_zoom", position=1)["isError"]
    assert tools.camera is camera


def test_real_process_keeps_stdout_clean(tmp_path):
    """The command a client runs, end to end: nothing but protocol messages on stdout."""
    lines = [{"id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
             {"method": "notifications/initialized"},
             {"id": 2, "method": "tools/call", "params": {"name": "act_shoot_photo"}},
             {"id": 3, "method": "tools/call", "params": {"name": "read_file_list"}}]
    done = subprocess.run([sys.executable, "-m", "studio_utility", "mcp", "--mock", "--download-dir", str(tmp_path)],
                          input="".join(json.dumps({"jsonrpc": "2.0", **m}) + "\n" for m in lines),
                          capture_output=True, text=True, timeout=60, env={**os.environ})
    replies = [json.loads(line) for line in done.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3], done.stderr
    assert json.loads(replies[2]["result"]["content"][0]["text"])["total"] == 4


def test_through_the_hub_transport(hub, tmp_path):
    """Same tools against a real hub server holding the mock camera, the way auto.connect() finds it on this Mac."""
    h, base = hub

    def connect():
        camera = HubTransport(f"{base}/?token={h.token}", device="AI test")
        camera.connect()
        return camera
    tools = CameraTools(connect, tmp_path / "downloads")
    assert data(call(tools, "read_status"))["connected_via"] == "hub"
    assert data(call(tools, "act_set_setting", key="iso", value="800"))["applied"] is True
    assert not call(tools, "act_shoot_photo").get("isError")
    new = data(call(tools, "read_new_files", timeout=5))["new_files"]
    assert len(new) == 1 and new[0].endswith("IMG_0004.JPG")
    assert data(call(tools, "download_file", path=new[0]))["bytes"] > 0
    assert not call(tools, "act_start_recording").get("isError")
    assert not call(tools, "act_stop_recording").get("isError")
    tools.close()
