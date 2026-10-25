# Build the Studio Utility Android app (android\) on this PC with the Android SDK that's already here.
# From the Mac:  ssh wednesday-tb "powershell -NoProfile -ExecutionPolicy Bypass -File studio-utility\scripts\windows_build_android.ps1"
# Output: studio-utility\dist\StudioUtility.apk (debug-signed with this PC's key, so later builds install as updates)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$proj = Join-Path $root "android"

function First($paths) { $paths | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1 }

$sdk = First @($env:ANDROID_HOME, $env:ANDROID_SDK_ROOT, "$env:LOCALAPPDATA\Android\Sdk")
if (-not $sdk) { "Android SDK not found (looked at ANDROID_HOME and $env:LOCALAPPDATA\Android\Sdk)"; exit 1 }
$jdk = First @($env:JAVA_HOME, "C:\Program Files\Android\Android Studio\jbr", "C:\Program Files\Android\Android Studio\jre",
               (Get-ChildItem "C:\Program Files\Eclipse Adoptium", "C:\Program Files\Java", "C:\Program Files\Microsoft" -Directory -ErrorAction SilentlyContinue |
                Where-Object { Test-Path "$($_.FullName)\bin\javac.exe" } | Sort-Object Name -Descending | Select-Object -First 1 -ExpandProperty FullName))
if (-not $jdk) { "No JDK found: install Android Studio (it bundles one) or set JAVA_HOME"; exit 1 }
$env:JAVA_HOME = $jdk
$env:ANDROID_HOME = $sdk
"SDK:  $sdk"
"JDK:  $jdk"

# Gradle: the project's wrapper if present, else gradle on PATH, else one Android Studio already downloaded
$gradle = First @((Join-Path $proj "gradlew.bat"))
if (-not $gradle) {
    $gradle = (Get-Command gradle -ErrorAction SilentlyContinue).Source
    if (-not $gradle) {
        $gradle = Get-ChildItem "$env:USERPROFILE\.gradle\wrapper\dists" -Recurse -Filter gradle.bat -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'gradle-8\.([7-9]|\d\d)' } | Sort-Object FullName -Descending |
            Select-Object -First 1 -ExpandProperty FullName
    }
    if (-not $gradle) { "No Gradle 8.7+ found: open android\ once in Android Studio (it downloads Gradle), then rerun"; exit 1 }
}
"Gradle: $gradle"

Set-Content -Path (Join-Path $proj "local.properties") -Value ("sdk.dir=" + ($sdk -replace '\\', '\\\\')) -Encoding ASCII
Push-Location $proj
try {
    & $gradle --no-daemon -q assembleDebug
    if ($LASTEXITCODE) { "Build failed"; exit 1 }
} finally { Pop-Location }

$apk = Join-Path $proj "app\build\outputs\apk\debug\app-debug.apk"
New-Item -ItemType Directory -Force (Join-Path $root "dist") | Out-Null
$out = Join-Path $root "dist\StudioUtility.apk"
Copy-Item $apk $out -Force
"Built: $out ($([math]::Round((Get-Item $out).Length / 1MB, 1)) MB)"
