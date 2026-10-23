# Move the internet uplink (phone hotspot) from the built-in Wi-Fi to the TP-Link USB adapter,
# so the built-in card is free to broadcast the camnet hotspot. Safe over SSH via Thunderbolt.
# Usage: powershell -ExecutionPolicy Bypass -File windows_uplink_tplink.ps1 [-Phone "Nitish's Phone"]
param([string]$Phone = "Nitish's Phone", [string]$Usb = "Wi-Fi 2", [string]$Builtin = "Wi-Fi")

$tmp = Join-Path $env:TEMP "camctl-wlan"
New-Item -ItemType Directory -Force $tmp | Out-Null

"--- Copying '$Phone' Wi-Fi profile to $Usb"
netsh wlan export profile name="$Phone" interface="$Builtin" key=clear folder="$tmp" | Out-Null
$xml = Get-ChildItem $tmp -Filter *.xml | Sort-Object LastWriteTime | Select-Object -Last 1
if (-not $xml) { "Could not export the '$Phone' profile from $Builtin."; exit 1 }
netsh wlan add profile filename="$($xml.FullName)" interface="$Usb" user=all
Remove-Item $tmp -Recurse -Force   # the export contains the Wi-Fi key in clear text

"--- Connecting $Usb to '$Phone'"
netsh wlan connect name="$Phone" interface="$Usb"
Start-Sleep -Seconds 8
$up = Get-NetAdapter -Name $Usb
"$Usb status: $($up.Status)"
if ($up.Status -ne 'Up') { "TP-Link did not connect; leaving $Builtin as is."; exit 1 }

"--- Freeing $Builtin for the hotspot"
netsh wlan disconnect interface="$Builtin"
Start-Sleep -Seconds 3
Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue |
    ForEach-Object { "default route via $($_.NextHop) on $($_.InterfaceAlias)" }
"Done. Now run windows_hotspot.ps1 -Band 2.4 to restart camnet."
