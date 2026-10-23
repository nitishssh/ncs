# Let the Mac reach the camera through the Thunderbolt bridge: forwards 10.11.0.2:<port> on Windows
# to the camera's CCAPI port. Run in an elevated (Administrator) PowerShell on Windows.
# Usage: powershell -ExecutionPolicy Bypass -File windows_camera_proxy.ps1 -CameraIp 192.168.0.50 [-Port 8080] [-Remove]
param([Parameter(Mandatory)][string]$CameraIp, [int]$Port = 8080, [string]$ListenIp = "10.11.0.2", [switch]$Remove)

$admin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $admin) { "Needs Administrator: right-click PowerShell > Run as administrator."; exit 1 }

$rule = "camctl CCAPI via Thunderbolt"
if ($Remove) {
    netsh interface portproxy delete v4tov4 listenaddress=$ListenIp listenport=$Port | Out-Null
    Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
    "Removed forward $ListenIp`:$Port"; exit 0
}

"--- Checking camera at $CameraIp`:$Port"
if (-not (Test-NetConnection $CameraIp -Port $Port -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    "Camera not reachable from Windows. Check that Windows and the camera are on the same router and CCAPI is enabled."
    exit 1
}

netsh interface portproxy delete v4tov4 listenaddress=$ListenIp listenport=$Port 2>$null | Out-Null
netsh interface portproxy add v4tov4 listenaddress=$ListenIp listenport=$Port connectaddress=$CameraIp connectport=$Port
# Allow only the Thunderbolt subnet (the Mac) to use the forward
Remove-NetFirewallRule -DisplayName $rule -ErrorAction SilentlyContinue
New-NetFirewallRule -DisplayName $rule -Direction Inbound -Protocol TCP -LocalAddress $ListenIp -LocalPort $Port `
    -RemoteAddress 10.11.0.0/24 -Action Allow | Out-Null

"--- Active forwards"
netsh interface portproxy show v4tov4
"Mac: in camctl choose 'wifi' and enter $ListenIp"
