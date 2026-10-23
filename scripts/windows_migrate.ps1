# Move this PC from the old "camctl" install to Studio Utility (run from the NEW folder):
#   C:\Users\<you>\studio-utility\scripts\windows_migrate.ps1
# - stops and removes the old "camctl relay" / "camctl hub" scheduled tasks and firewall rules
# - creates the new Python environment here and installs Studio Utility
# - re-installs the relay (port 8090) and the hub (port 8765) as "studio-utility relay" / "studio-utility hub"
# Your hub pairing token, presets and noise profiles are carried over (~\.camctl -> ~\.studio-utility).
# The old C:\Users\<you>\camctl folder is left in place; delete it once everything works.
param([int]$RelayPort = 8090, [int]$HubPort = 8765)

$root = Split-Path $PSScriptRoot -Parent

"--- Removing the old camctl tasks and processes"
foreach ($t in "camctl relay", "camctl hub") {
    Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue
}
Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -match "ccapi_relay\.py|camctl\.hub" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; "  stopped pid $($_.ProcessId)" }
Get-NetFirewallRule -ErrorAction SilentlyContinue | Where-Object DisplayName -like "camctl*" |
    ForEach-Object { "  removing firewall rule '$($_.DisplayName)'"; Remove-NetFirewallRule -Name $_.Name }

"--- Installing Studio Utility in $root"
& "$root\scripts\windows_setup.cmd"
if ($LASTEXITCODE) { "Setup failed"; exit 1 }

"--- Relay (Thunderbolt -> camera) on port $RelayPort"
New-NetFirewallRule -DisplayName "studio-utility relay $RelayPort (Thunderbolt)" -Direction Inbound -Protocol TCP `
    -LocalPort $RelayPort -LocalAddress 10.11.0.2 -RemoteAddress 10.11.0.0/24 -Action Allow | Out-Null
& "$root\scripts\windows_relay_autostart.ps1" -ListenPort $RelayPort

"--- Hub on port $HubPort"
& "$root\scripts\windows_hub_autostart.ps1" -Port $HubPort
