# AI control (MCP)

`studio-utility mcp` is an [MCP](https://modelcontextprotocol.io) server that lets an AI client control the camera. It finds the camera the same way the app's "auto" link does: through the hub on this computer if it holds the camera, otherwise directly over USB.

## Tools
| Kind | Tools | Effect |
|---|---|---|
| `read_*` | `read_status`, `read_settings`, `read_liveview_frame`, `read_file_list`, `read_thumbnail`, `read_new_files` | Don't change the camera |
| Local | `download_file`, `release_camera` | Don't change the camera. `download_file` saves to this computer and never overwrites a file. |
| `act_*` | `act_shoot_photo`, `act_start_recording`, `act_stop_recording`, `act_set_setting`, `act_autofocus`, `act_focus_at`, `act_focus_nudge`, `act_zoom` | Change the camera |

No tool deletes or formats card contents. Start the server with `--read-only` to leave out every `act_*` tool. Which tools work depends on the link: over USB there is no `act_zoom` or `act_focus_at`, and `read_status` lists what's available.

## Setup
First install Studio Utility as in the README, then pick one:

**Claude Code plugin (macOS, Linux).** From the repository root, for one session:
```bash
claude --plugin-dir plugin
```
It uses `.venv` in the repository. For another Python, set `STUDIO_UTILITY_PYTHON` to its path.

**Claude Code, without the plugin (any system):**
```bash
claude mcp add camera -- /path/to/.venv/bin/python -m studio_utility mcp
```

**Other MCP clients, such as Claude Desktop:**
```json
{"mcpServers": {"camera": {"command": "/path/to/.venv/bin/python", "args": ["-m", "studio_utility", "mcp"]}}}
```

## Options
- `--read-only`: leave out the `act_*` tools.
- `--download-dir PATH`: where `download_file` saves. The default is `~/Pictures/Studio Utility AI`.
- `--mock`: use the fake camera, to try the setup with no hardware.

## Good to know
- **Run the hub if other apps need the camera.** USB allows one owner. Without a hub, this server holds the camera from its first camera tool until it exits or `release_camera` is called.
- **Your AI client's approvals are the safeguard for `act_*` tools.** Keep approval prompts on for them if you don't want the AI acting on its own.
- **A failed action is not retried.** If the link drops during an action, the server says the action may or may not have happened, and reconnects on the next tool call.
- **`act_shoot_photo` means "accepted".** `read_new_files` confirms that a file arrived.
