# Turn the Windows PC into the camera network hub (Windows "Mobile hotspot").
# Internet comes in on one Wi-Fi adapter (phone hotspot); the hotspot is broadcast for the camera and Mac.
# Usage:  powershell -ExecutionPolicy Bypass -File windows_hotspot.ps1 [-Ssid camnet] [-NewPassword | -Password secret123] [-Stop]
param([string]$Ssid, [string]$Password, [ValidateSet('2.4', '5', 'auto')][string]$Band,
      [switch]$NewPassword, [switch]$Stop)

if ($NewPassword) {
    # Random 12-char password without look-alike characters, easy to type on the camera keyboard.
    $chars = 'abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789'.ToCharArray()
    $Password = -join (1..12 | ForEach-Object { $chars | Get-Random })
}

Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
function Await($op, [Type]$type) {
    $t = $asTask.MakeGenericMethod($type).Invoke($null, @($op)); $t.Wait(-1) | Out-Null; $t.Result
}

"--- Wi-Fi adapters"
Get-NetAdapter -Physical | Where-Object { $_.MediaType -match '802.11' -or $_.Name -match 'Wi-?Fi' } |
    ForEach-Object { "{0} | {1} | {2}" -f $_.Name, $_.InterfaceDescription, $_.Status }

$profile = [Windows.Networking.Connectivity.NetworkInformation, Windows.Networking.Connectivity, ContentType = WindowsRuntime]::GetInternetConnectionProfile()
if (-not $profile) { "No internet connection to share. Connect Windows to the phone hotspot first."; exit 1 }
"Sharing internet from: $($profile.ProfileName)"

$mgr = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager, Windows.Networking.NetworkOperators, ContentType = WindowsRuntime]::CreateFromConnectionProfile($profile)

if ($Stop) {
    Await ($mgr.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]) | Out-Null
    "Hotspot stopped."; exit 0
}

# Windows turns the hotspot off after ~5 min with no clients; the camera may take longer to join.
try { [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager]::DisableNoConnectionsTimeout(); "Auto-off: disabled" }
catch { "Auto-off: could not disable ($($_.Exception.Message))" }

if ($Ssid -or $Password -or $Band) {
    $cfg = $mgr.GetCurrentAccessPointConfiguration()
    if ($Ssid) { $cfg.Ssid = $Ssid }
    if ($Password) { $cfg.Passphrase = $Password }
    if ($Band) {
        $cfg.Band = @{ '2.4' = 'TwoPointFourGigahertz'; '5' = 'FiveGigahertz'; 'auto' = 'Auto' }[$Band]
        if ($mgr.TetheringOperationalState -eq 'On') {
            # Band changes only apply after a restart of the hotspot
            Await ($mgr.StopTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]) | Out-Null
        }
    }
    $actionTask = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
    $actionTask.Invoke($null, @($mgr.ConfigureAccessPointAsync($cfg))).Wait(-1) | Out-Null
    "Hotspot name/password updated."
}

if ($mgr.TetheringOperationalState -ne 'On') {
    $r = Await ($mgr.StartTetheringAsync()) ([Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult])
    "Start result: $($r.Status) $($r.AdditionalErrorMessage)"
}

$cfg = $mgr.GetCurrentAccessPointConfiguration()
"--- Hotspot"
"State:    $($mgr.TetheringOperationalState)"
"SSID:     $($cfg.Ssid)"
"Password: $($cfg.Passphrase)"
"Band:     $($cfg.Band)"
"Clients:  $($mgr.ClientCount) / $($mgr.MaxClientCount)"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -like '192.168.137.*' |
    ForEach-Object { "Hub IP:   $($_.IPAddress) on $($_.InterfaceAlias)" }
