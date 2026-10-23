# Find the Canon camera (CCAPI on port 8080) on the router network this PC is plugged into.
# Usage: powershell -ExecutionPolicy Bypass -File windows_find_camera.ps1 [-Port 8080]
param([int]$Port = 8080)

# The router LAN: any IPv4 except Thunderbolt (10.11.*), phone hotspot (10.63.*), WSL (172.*),
# link-local (169.254.*) and VPNs such as Tailscale (100.64.0.0/10)
$lan = Get-NetIPAddress -AddressFamily IPv4 | Where-Object {
    $_.IPAddress -notmatch '^(127|169\.254|10\.11|10\.63|172\.)' -and
    $_.IPAddress -notmatch '^100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.' -and
    $_.InterfaceAlias -notmatch 'Tailscale|vEthernet' } | Select-Object -First 1
if (-not $lan) { "No router network found. Is the Ethernet adapter plugged into the router and set to DHCP?"; exit 1 }
$prefix = ($lan.IPAddress -split '\.')[0..2] -join '.'
"This PC: $($lan.IPAddress) on $($lan.InterfaceAlias)  (scanning $prefix.1-254)"

# Ping sweep in parallel to fill the ARP table
$pings = 1..254 | ForEach-Object { $p = New-Object System.Net.NetworkInformation.Ping; @{ ip = "$prefix.$_"; t = $p.SendPingAsync("$prefix.$_", 400) } }
[System.Threading.Tasks.Task]::WaitAll([System.Threading.Tasks.Task[]]($pings | ForEach-Object { $_.t })) 2>$null
$alive = $pings | Where-Object { $_.t.Result.Status -eq 'Success' } | ForEach-Object { $_.ip }
$alive += Get-NetNeighbor -AddressFamily IPv4 -IPAddress "$prefix.*" -ErrorAction SilentlyContinue |
    Where-Object State -in 'Reachable','Stale','Delay' | ForEach-Object IPAddress
$alive = $alive | Sort-Object -Unique
"Devices: $($alive -join ', ')"

"--- Checking port $Port"
$found = $false
foreach ($ip in $alive) {
    $c = New-Object System.Net.Sockets.TcpClient
    if ($c.ConnectAsync($ip, $Port).Wait(500) -and $c.Connected) {
        $c.Close()
        try {
            $r = Invoke-WebRequest "http://${ip}:$Port/ccapi" -UseBasicParsing -TimeoutSec 5
            "CAMERA FOUND: $ip  (CCAPI responded, $($r.Content.Length) bytes)"
            $found = $true
        } catch { "$ip has port $Port open but /ccapi did not answer: $($_.Exception.Message)" }
    } else { $c.Close() }
}
if (-not $found) { "No CCAPI camera found. Check the camera is joined to the router Wi-Fi with Camera Control API enabled." }
