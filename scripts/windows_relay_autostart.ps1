# Make the camctl relay permanent: start it hidden at Windows login and restart it if it stops.
# Also stops any relays started by hand, then starts the task now.
# Usage:  powershell -ExecutionPolicy Bypass -File windows_relay_autostart.ps1 [-ListenPort 8090] [-Remove]
param([string]$Camera = "fe80::7eb8:daff:fe30:f748", [string]$Iface = "Ethernet 3",
      [int]$ListenPort = 8090, [switch]$Remove)

$name = "camctl relay"
$root = Split-Path $PSScriptRoot -Parent           # ...\camctl
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"  # no console window

# Stop relays started by hand (any port), so the task owns the port
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*ccapi_relay.py*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force; "Stopped manual relay pid $($_.ProcessId)" }

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    "Removed scheduled task '$name'."; exit 0
}
if (-not (Test-Path $pyw)) { "Not found: $pyw (run scripts\windows_setup.cmd first)"; exit 1 }

$argList = "scripts\ccapi_relay.py --camera $Camera --iface `"$Iface`" --listen-port $ListenPort"
$action = New-ScheduledTaskAction -Execute $pyw -Argument $argList -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Forwards the Canon R50 V CCAPI to the Mac over Thunderbolt (10.11.0.2:$ListenPort)" -Force | Out-Null
Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 3

$listen = Get-NetTCPConnection -LocalPort $ListenPort -State Listen -ErrorAction SilentlyContinue
if ($listen) { "OK: '$name' runs at login and is listening on $($listen[0].LocalAddress):$ListenPort" }
else { "Task registered, but nothing is listening on $ListenPort yet. Check Task Scheduler > '$name' > History." }
