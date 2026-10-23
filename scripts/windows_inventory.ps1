# Read-only inventory of the Windows PC for Studio Utility. Run: ssh wednesday-tb "powershell -NoProfile -" < scripts/windows_inventory.ps1
$c = Get-CimInstance Win32_ComputerSystem; $o = Get-CimInstance Win32_OperatingSystem
"OS: $($o.Caption) $($o.Version)"
"Machine: $($c.Manufacturer) $($c.Model), RAM $([math]::Round($c.TotalPhysicalMemory/1GB)) GB"
"CPU: $((Get-CimInstance Win32_Processor).Name)"
"GPU: $((Get-CimInstance Win32_VideoController | ForEach-Object Name) -join ', ')"
"--- Network"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object IPAddress -notlike '127*' | ForEach-Object { "$($_.InterfaceAlias): $($_.IPAddress)/$($_.PrefixLength)" }
Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | ForEach-Object { "default via $($_.NextHop) on $($_.InterfaceAlias)" }
"--- Cameras / imaging"
Get-PnpDevice -Class Camera,Image -PresentOnly -ErrorAction SilentlyContinue | ForEach-Object FriendlyName
"--- Bluetooth"
Get-PnpDevice -Class Bluetooth -PresentOnly -ErrorAction SilentlyContinue | Select-Object -First 3 | ForEach-Object FriendlyName
"--- Software"
foreach ($cmd in 'python','py','git','ffmpeg') { $p = Get-Command $cmd -ErrorAction SilentlyContinue; if ($p) { "$cmd : $($p.Source)" } else { "$cmd : missing" } }
if (Test-Path 'C:\Program Files\obs-studio') { 'OBS: installed' } else { 'OBS: missing' }
Get-ChildItem 'C:\Program Files\Canon','C:\Program Files (x86)\Canon' -ErrorAction SilentlyContinue | ForEach-Object { "Canon: $($_.Name)" }
