"""MCP server: lets an AI client (Claude Code, Claude Desktop, ...) control the camera through the normal transports.

Speaks MCP over stdio (one JSON-RPC message per line), with no SDK so the app gains no dependency. Tool names say
what a tool does to the camera: read_* never change it, act_* do. There is deliberately no tool that deletes or
formats card contents. Setup: docs/AI_CONTROL.md."""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import BinaryIO, Callable

import httpx

from studio_utility.core import CameraLost, NotSupported, Transport
from studio_utility.transports import auto

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
DEFAULT_DOWNLOADS = Path.home() / "Pictures" / "Studio Utility AI"
# What read_settings reads when not given keys; a body or link that lacks one reports it under "unavailable"
DEFAULT_SETTINGS = ("mode", "movie_size", "iso", "tv", "av", "exposure", "wb", "af_method", "picture_style")
MAX_WAIT_S = 60
READ_ONLY_NOTE = "Read-only: does not change the camera."
ACT_NOTE = "CHANGES THE CAMERA: use only when the user asked for this."


def _schema(properties: dict | None = None, required: tuple[str, ...] = ()) -> dict:
    return {"type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False}


def _tool(name: str, description: str, schema: dict | None = None, *, reads: bool = False, hint: bool = False) -> dict:
    """reads: leaves camera and computer untouched. hint: can lose something (stopping a take in progress)."""
    return {"name": name, "description": description, "inputSchema": schema or _schema(),
            "annotations": {"readOnlyHint": reads, "destructiveHint": hint, "openWorldHint": False}}


READ_TOOLS = [
    _tool("read_status", f"Camera model, connection, capabilities, battery, card space and recording state. "
          f"{READ_ONLY_NOTE}", reads=True),
    _tool("read_settings", f"Current value and allowed values of camera settings. {READ_ONLY_NOTE}",
          _schema({"keys": {"type": "array", "items": {"type": "string"},
                            "description": f"Setting names. Default: {', '.join(DEFAULT_SETTINGS)}"}}), reads=True),
    _tool("read_liveview_frame", f"One JPEG of what the camera sees now. {READ_ONLY_NOTE}", reads=True),
    _tool("read_file_list", f"Newest files on the card, oldest first. {READ_ONLY_NOTE}",
          _schema({"limit": {"type": "integer", "minimum": 1, "description": "How many, default 50"}}), reads=True),
    _tool("read_thumbnail", f"Small JPEG preview of a file on the card. {READ_ONLY_NOTE}",
          _schema({"path": {"type": "string", "description": "Path from read_file_list"}}, ("path",)), reads=True),
    _tool("read_new_files", f"Wait for files to appear on the card, such as after act_shoot_photo. May also return "
          f"files other devices added since the last check. {READ_ONLY_NOTE}",
          _schema({"timeout": {"type": "number", "minimum": 0, "maximum": MAX_WAIT_S,
                               "description": "Seconds to wait, default 10"}}), reads=True),
]
LOCAL_TOOLS = [
    _tool("download_file", "Copy a file from the card to the download folder on this computer and return its local "
          "path. Does not change the camera and never overwrites a local file.",
          _schema({"path": {"type": "string", "description": "Path from read_file_list"}}, ("path",))),
    _tool("release_camera", "Close this server's camera connection so another app can take a USB camera. The next "
          "camera tool reconnects. Does not change the camera or stop a recording."),
]
ACT_TOOLS = [
    _tool("act_shoot_photo", f"{ACT_NOTE} Release the shutter, adding a photo to the card. The result only means the "
          "camera accepted the command: check with read_new_files.",
          _schema({"autofocus": {"type": "boolean", "description": "Focus first, default true"}})),
    _tool("act_start_recording", f"{ACT_NOTE} Start recording a movie to the card. The camera must be in a movie "
          "mode. Recording continues until act_stop_recording."),
    _tool("act_stop_recording", f"{ACT_NOTE} Stop the movie being recorded, even one another device started.",
          hint=True),
    _tool("act_set_setting", f"{ACT_NOTE} Change one setting. Use a value from read_settings. The result reports the "
          "value the camera has afterwards.",
          _schema({"key": {"type": "string"}, "value": {"type": "string"}}, ("key", "value"))),
    _tool("act_autofocus", f"{ACT_NOTE} Autofocus without taking a photo."),
    _tool("act_focus_at", f"{ACT_NOTE} Move the AF point to a live view position and focus.",
          _schema({"x": {"type": "number", "minimum": 0, "maximum": 1, "description": "0 left .. 1 right"},
                   "y": {"type": "number", "minimum": 0, "maximum": 1, "description": "0 top .. 1 bottom"}},
                  ("x", "y"))),
    _tool("act_focus_nudge", f"{ACT_NOTE} Nudge manual focus: -3..-1 nearer, 1..3 farther, bigger is coarser.",
          _schema({"step": {"type": "integer", "minimum": -3, "maximum": 3}}, ("step",))),
    _tool("act_zoom", f"{ACT_NOTE} Move a power zoom lens: 0 wide .. 100 tele.",
          _schema({"position": {"type": "integer", "minimum": 0, "maximum": 100}}, ("position",))),
]


def _text(value) -> dict:
    return {"type": "text", "text": value if isinstance(value, str) else json.dumps(value, indent=2, default=str)}


def _image(jpeg: bytes) -> dict:
    return {"type": "image", "data": base64.b64encode(jpeg).decode(), "mimeType": "image/jpeg"}


class CameraTools:
    """The tools, on one lazily opened transport. The connection opens at the first camera tool, not at start-up,
    because a direct USB connection keeps every other app off the camera."""

    def __init__(self, connect: Callable[[], Transport] = auto.connect, downloads: Path = DEFAULT_DOWNLOADS,
                 read_only: bool = False):
        self.open_camera = connect
        self.downloads = Path(downloads)
        self.read_only = read_only
        self.camera: Transport | None = None

    def tools(self) -> list[dict]:
        return READ_TOOLS + LOCAL_TOOLS + ([] if self.read_only else ACT_TOOLS)

    def cam(self) -> Transport:
        if self.camera is None:
            self.camera = self.open_camera()
        return self.camera

    def close(self) -> None:
        camera, self.camera = self.camera, None
        if camera:
            try:
                camera.disconnect()
            except Exception:  # noqa: BLE001 - the link is already gone
                pass

    def call(self, name: str, args: dict) -> dict:
        """MCP tool result. Failures go back to the model as text. A failed action is never retried here: it may have
        reached the camera, and a second attempt could take a second photo."""
        method = getattr(self, f"tool_{name}")
        try:
            result = method(**args)
        except TypeError as error:  # wrong or missing argument
            return {"content": [_text(f"{name}: {error}")], "isError": True}
        except NotSupported as error:
            return {"content": [_text(f"Not supported over this connection: {error}")], "isError": True}
        except (CameraLost, httpx.TransportError) as error:
            self.close()
            return {"content": [_text(f"Lost the camera link ({error}). Check read_status before acting again: "
                                      "the last action may or may not have happened.")], "isError": True}
        except Exception as error:  # noqa: BLE001 - tell the model, keep serving
            return {"content": [_text(str(error) or type(error).__name__)], "isError": True}
        return {"content": result if isinstance(result, list) else [_text(result)]}

    # read_*
    def tool_read_status(self) -> dict:
        camera = self.cam()
        info = camera.info()
        return {"connected_via": camera.name, "model": info.model, "firmware": info.firmware,
                "capabilities": sorted(camera.capabilities()), "status": camera.status()}

    def tool_read_settings(self, keys: list[str] | None = None) -> dict:
        camera = self.cam()
        settings, unavailable = {}, {}
        for key in keys or DEFAULT_SETTINGS:
            try:
                settings[key] = camera.get_setting(key)
            except (CameraLost, httpx.TransportError):
                raise
            except Exception as error:  # noqa: BLE001 - one unanswered key shouldn't hide the others
                unavailable[key] = str(error) or type(error).__name__
        return {"settings": settings, "unavailable": unavailable}

    def tool_read_liveview_frame(self) -> list[dict]:
        return [_image(self.cam().liveview_frame())]

    def tool_read_file_list(self, limit: int = 50) -> dict:
        files = self.cam().list_files()
        return {"total": len(files), "files": files[-max(1, int(limit)):]}

    def tool_read_thumbnail(self, path: str) -> list[dict]:
        return [_image(self.cam().thumbnail(path))]

    def tool_read_new_files(self, timeout: float = 10) -> dict:
        # returns early on any camera event, such as a battery change, so keep waiting until files show up
        camera = self.cam()
        end = time.monotonic() + min(max(float(timeout), 0), MAX_WAIT_S)
        while True:
            left = end - time.monotonic()
            found = camera.wait_new_files(max(left, 0))
            if found or left <= 0:
                return {"new_files": found}

    # local
    def tool_download_file(self, path: str) -> dict:
        camera = self.cam()
        if path not in camera.list_files():  # only real card paths: this also keeps odd paths away from local folders
            raise ValueError(f"not on the card: {path} (use a path from read_file_list)")
        name = path.rsplit("/", 1)[-1]
        out = self.downloads / name
        if out.exists():
            raise FileExistsError(f"{out} already exists: not overwriting it")
        saved = camera.download(path, self.downloads)
        return {"saved_to": str(saved), "bytes": saved.stat().st_size}

    def tool_release_camera(self) -> dict:
        self.close()
        return {"released": True}

    # act_*: in read-only mode they aren't listed and handle() refuses them
    def tool_act_shoot_photo(self, autofocus: bool = True) -> dict:
        self.cam().shoot(autofocus=bool(autofocus))
        return {"accepted": True}

    def tool_act_start_recording(self) -> dict:
        camera = self.cam()
        if camera.status().get("recording") is not None:
            raise RuntimeError("the camera is already recording")
        camera.record(True)
        return {"recording": True}

    def tool_act_stop_recording(self) -> dict:
        self.cam().record(False)
        return {"recording": False}

    def tool_act_set_setting(self, key: str, value: str) -> dict:
        camera = self.cam()
        camera.set_setting(key, str(value))
        now = camera.get_setting(key).get("value")
        return {"key": key, "requested": str(value), "value": now, "applied": now == str(value)}

    def tool_act_autofocus(self) -> dict:
        self.cam().half_press()
        return {"accepted": True}

    def tool_act_focus_at(self, x: float, y: float) -> dict:
        self.cam().focus_at(float(x), float(y))
        return {"accepted": True}

    def tool_act_focus_nudge(self, step: int) -> dict:
        self.cam().focus_drive(int(step))
        return {"accepted": True}

    def tool_act_zoom(self, position: int) -> dict:
        self.cam().zoom(int(position))
        return {"accepted": True}


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def handle(tools: CameraTools, method: str, params: dict) -> dict:
    """The result of one JSON-RPC request."""
    if method == "initialize":
        asked = params.get("protocolVersion")
        return {"protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "studio-utility-camera", "version": "0.1.0"},
                "instructions": "Controls a real camera. read_* tools never change it. act_* tools do: use them only "
                                "when the user asked for that action. Nothing here can delete card contents."}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": tools.tools()}
    if method == "tools/call":
        name = params.get("name")
        if name not in {tool["name"] for tool in tools.tools()}:
            raise RpcError(-32602, f"unknown tool: {name}")
        return tools.call(name, params.get("arguments") or {})
    raise RpcError(-32601, f"method not found: {method}")


def respond(tools: CameraTools, line: bytes) -> dict | None:
    """The reply to one line of input; None for a notification, which gets no reply."""
    try:
        message = json.loads(line)
        if not isinstance(message, dict):
            raise ValueError("not an object")
    except ValueError as error:
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"parse error: {error}"}}
    if "id" not in message:
        return None
    try:
        result = handle(tools, message.get("method", ""), message.get("params") or {})
    except RpcError as error:
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": error.code, "message": error.message}}
    except Exception as error:  # noqa: BLE001 - a malformed request must not end the server
        return {"jsonrpc": "2.0", "id": message["id"], "error": {"code": -32603, "message": f"bad request: {error}"}}
    return {"jsonrpc": "2.0", "id": message["id"], "result": result}


def serve(tools: CameraTools, reader: BinaryIO, writer: BinaryIO) -> None:
    for line in reader:
        if not line.strip():
            continue
        reply = respond(tools, line)
        if reply is not None:
            writer.write(json.dumps(reply, separators=(",", ":")).encode() + b"\n")
            writer.flush()


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="studio-utility mcp", description="MCP server for AI control of the camera")
    p.add_argument("--read-only", action="store_true", help="leave out every tool that changes the camera")
    p.add_argument("--download-dir", type=Path, default=DEFAULT_DOWNLOADS, help="where download_file saves")
    p.add_argument("--mock", action="store_true", help="use the fake camera, to try the setup without hardware")
    a = p.parse_args(argv)

    # stdout carries only protocol messages: send anything else that prints there, native SDKs included, to stderr
    protocol = os.fdopen(os.dup(1), "wb")
    os.dup2(2, 1)
    sys.stdout = sys.stderr

    def open_mock() -> Transport:
        from studio_utility.transports.mock import MockTransport
        camera = MockTransport()
        camera.connect()
        return camera

    tools = CameraTools(open_mock if a.mock else auto.connect, a.download_dir, a.read_only)
    try:
        serve(tools, sys.stdin.buffer, protocol)
    finally:
        tools.close()


if __name__ == "__main__":
    main()
