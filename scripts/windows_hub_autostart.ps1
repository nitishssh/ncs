# Run the Studio Utility Hub on this PC permanently: hidden at login, restarted if it stops.
# The hub reaches the camera through the studio-utility relay (10.11.0.2:8090), which must also be installed
# (windows_relay_autostart.ps1). Phones/Macs/PCs then open http://<this PC>:8765.
# Usage:  powershell -ExecutionPolicy Bypass -File windows_hub_autostart.ps1 [-Port 8765] [-Remove]
param([int]$Port = 8765, [string]$Camera = "10.11.0.2:8090", [switch]$Remove)

$name = "studio-utility hub"
$root = Split-Path $PSScriptRoot -Parent
$py = Join-Path $root ".venv\Scripts\python.exe"
$pyw = Join-Path $root ".venv\Scripts\pythonw.exe"
$rule = "studio-utility hub ($Port)"

Get-CimInstance Win32_Process -Filter "Name like 'python%'" |
    Where-Object { $_.CommandLine -like "*studio_utility.hub*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

if ($Remove) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
    "Removed '$name' and its firewall rule."; exit 0
}
if (-not (Test-Path $py)) { "Not found: $py (run scripts\windows_setup.cmd first)"; exit 1 }

"--- Installing hub dependencies"
& $py -m pip install -q --disable-pip-version-check -e $root
if ($LASTEXITCODE) { "pip install failed"; exit 1 }

# Home networks only: phone hotspot, Thunderbolt link to the Mac, camera router, Tailscale.
# Every request still needs the pairing token.
Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalPort $Port -Action Allow `
    -RemoteAddress @("10.63.196.0/24", "10.11.0.0/24", "192.168.0.0/24", "100.64.0.0/10", "LocalSubnet") | Out-Null

$action = New-ScheduledTaskAction -Execute $pyw -Argument "-m studio_utility.hub --camera $Camera --port $Port" -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings `
    -Description "Studio Utility Hub: shares the Canon camera with phone, Mac and Windows (port $Port)" -Force | Out-Null
Start-ScheduledTask -TaskName $name

$ok = $false
for ($i = 0; $i -lt 20 -and -not $ok; $i++) {
    Start-Sleep -Milliseconds 500
    $ok = [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}
if (-not $ok) { "Task registered but nothing listens on $Port yet. Check Task Scheduler > '$name' > History."; exit 1 }

$token = (Get-Content "$env:USERPROFILE\.studio-utility\hub.json" | ConvertFrom-Json).token
"OK: '$name' runs at login on port $Port."
"Pairing links (phone: open on the phone / desktop Studio Utility: choose 'hub' and paste):"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notmatch '^(127|169\.254|172\.)' } |
    ForEach-Object { "  http://$($_.IPAddress):$Port/?token=$token    ($($_.InterfaceAlias))" }
"QR codes: open http://localhost:$Port/pair on this PC."
