# Build Studio Utility and install it for this Windows user, with Start menu and desktop shortcuts.
# No administrator rights needed: installs to %LOCALAPPDATA%\Programs\Studio Utility.
# Usage (from the Mac):  ssh wednesday-tb "powershell -NoProfile -ExecutionPolicy Bypass -File studio-utility\scripts\windows_install_app.ps1"
param([switch]$SkipBuild, [switch]$Remove)

$root = Split-Path $PSScriptRoot -Parent
$dest = Join-Path $env:LOCALAPPDATA "Programs\Studio Utility"
$startMenu = Join-Path ([Environment]::GetFolderPath("Programs")) "Studio Utility.lnk"
$desktop = Join-Path ([Environment]::GetFolderPath("Desktop")) "Studio Utility.lnk"

Get-Process studio-utility -ErrorAction SilentlyContinue | Stop-Process -Force  # can't replace a running app

if ($Remove) {
    Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
    Remove-Item $startMenu, $desktop -Force -ErrorAction SilentlyContinue
    "Studio Utility removed."; exit 0
}

if (-not $SkipBuild) {
    "--- Building (a few minutes)"
    & cmd /c "`"$root\scripts\build_windows.cmd`""
    if ($LASTEXITCODE) { "Build or self-test failed - see above"; exit 1 }
}
$built = Join-Path $root "dist\studio-utility"
if (-not (Test-Path "$built\studio-utility.exe")) { "Not built: $built\studio-utility.exe"; exit 1 }

"--- Installing to $dest"
Remove-Item $dest -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force (Split-Path $dest) | Out-Null
Copy-Item $built $dest -Recurse

$shell = New-Object -ComObject WScript.Shell
foreach ($lnk in $startMenu, $desktop) {
    $s = $shell.CreateShortcut($lnk)
    $s.TargetPath = Join-Path $dest "studio-utility.exe"
    $s.WorkingDirectory = $dest
    $s.Description = "Studio Utility - control your Canon camera"
    $s.IconLocation = (Join-Path $dest "studio-utility.exe") + ",0"
    $s.Save()
}
"OK: Studio Utility installed. Start menu and desktop shortcuts created."
$token = (Get-Content "$env:USERPROFILE\.studio-utility\hub.json" -ErrorAction SilentlyContinue | ConvertFrom-Json).token
if ($token) { "In the app choose 'hub' and paste:  http://localhost:8765/?token=$token" }
