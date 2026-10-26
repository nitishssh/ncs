#!/bin/bash
# Run the Studio Utility Hub on this Mac permanently: at login, restarted if it stops.
# The Mac counterpart of windows_hub_autostart.ps1. A LaunchAgent (not a daemon) because Canon's SDK reaches
# USB cameras through ImageCaptureCore, which needs the logged-in user's session. When the camera drops off
# USB, ImageCaptureCore can kill the process (EXC_BREAKPOINT in xpc_connection_resume, found 2026-10-26);
# launchd then starts the hub again and it waits for the camera.
# Usage:  scripts/mac_hub_autostart.sh [--port 8765] [--camera 10.11.0.2:8090] [--remove]
#         PYTHON=/path/to/python scripts/mac_hub_autostart.sh   (default: .venv/bin/python in this folder)
set -euo pipefail

port=8765
camera="10.11.0.2:8090"
remove=0
while [ $# -gt 0 ]; do
    case "$1" in
        --port) port="$2"; shift 2 ;;
        --camera) camera="$2"; shift 2 ;;
        --remove) remove=1; shift ;;
        *) echo "unknown option: $1"; exit 2 ;;
    esac
done

label="com.studio-utility.hub"
root="$(cd "$(dirname "$0")/.." && pwd)"
py="${PYTHON:-$root/.venv/bin/python}"
plist="$HOME/Library/LaunchAgents/$label.plist"
logs="$HOME/.studio-utility/logs"
domain="gui/$(id -u)"

launchctl bootout "$domain/$label" 2>/dev/null || true
if [ "$remove" = 1 ]; then
    rm -f "$plist"
    echo "Removed '$label'."; exit 0
fi
if [ ! -x "$py" ]; then echo "Not found: $py (create the venv, or set PYTHON)"; exit 1; fi

mkdir -p "$logs" "$(dirname "$plist")"
# KeepAlive restarts it whenever it exits; ThrottleInterval waits 10 s between starts so a crash loop stays cheap.
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$label</string>
    <key>ProgramArguments</key>
    <array>
        <string>$py</string><string>-m</string><string>studio_utility.hub</string>
        <string>--camera</string><string>$camera</string>
        <string>--port</string><string>$port</string>
        <string>--usb</string>
    </array>
    <key>WorkingDirectory</key><string>$root</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>ProcessType</key><string>Interactive</string>
    <key>StandardOutPath</key><string>$logs/hub.log</string>
    <key>StandardErrorPath</key><string>$logs/hub.log</string>
</dict>
</plist>
EOF
launchctl bootstrap "$domain" "$plist"

for _ in $(seq 60); do
    if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "OK: '$label' runs at login on port $port and restarts if it stops (log: $logs/hub.log)."
        exit 0
    fi
    sleep 0.5
done
echo "Registered, but nothing listens on $port yet. See $logs/hub.log"; exit 1
