# AISignX - Build Clients (Windows)
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Electron -NoBump
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Android
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -BumpOnly
#
# Options:
#   -Electron     Build/copy Electron (Windows .exe; Linux artifacts if present in dist)
#   -Android      Build/copy Android APK
#   (default)     Both Electron and Android if neither switch is set
#   -NoBump       Do not increment versions in source files
#   -BumpOnly     Bump versions + update client_versions.json only (no compile)
#   -Help         Show usage

param(
    [switch]$Help,
    [switch]$NoBump,
    [switch]$BumpOnly,
    [switch]$Electron,
    [switch]$Android
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Header($msg) {
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
}

function Write-Step($msg) { Write-Host "[*] $msg" -ForegroundColor Yellow }
function Write-OK($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Skip($msg) { Write-Host "[SKIP] $msg" -ForegroundColor DarkGray }
function Write-Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red }

function Bump-SemverPatch([string]$Version) {
    if ($Version -match '^(\d+)\.(\d+)\.(\d+)$') {
        return ('{0}.{1}.{2}' -f $Matches[1], $Matches[2], ([int]$Matches[3] + 1))
    }
    if ($Version -match '^(\d+)\.(\d+)$') {
        return ('{0}.{1}' -f $Matches[1], ([int]$Matches[2] + 1))
    }
    throw "Cannot bump version (expected x.y.z or x.y): $Version"
}

function Show-BuildHelp {
    Write-Host @"

AISignX client build script (Windows)

Usage:
  powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 [options]

Options:
  -Electron      Build Electron (Windows installer; copy Linux artifacts from dist if present)
  -Android       Build Android debug APK
  (no target)    Build both Electron and Android

  -NoBump        Skip version bump in package.json / build.gradle.kts
  -BumpOnly      Only bump versions and update client_versions.json (no compile)
  -Help          Show this help

Examples:
  .\build_clients_windows.ps1                      # bump + build all
  .\build_clients_windows.ps1 -Electron -NoBump    # rebuild Windows installer only
  .\build_clients_windows.ps1 -Android             # bump + build APK only
  .\build_clients_windows.ps1 -BumpOnly -Electron  # bump Electron version + manifest only

"@
}

function Get-ElectronVersion {
    param([string]$PkgPath)
    $raw = Get-Content $PkgPath -Raw
    if ($raw -notmatch '"version"\s*:\s*"([^"]+)"') { throw "version not found in package.json" }
    return $Matches[1]
}

function Get-AndroidVersion {
    param([string]$GradlePath)
    $raw = Get-Content $GradlePath -Raw
    if ($raw -notmatch 'versionName\s*=\s*"([^"]+)"') { throw "versionName not found in build.gradle.kts" }
    return $Matches[1]
}

function Invoke-BumpElectronVersion {
    param([string]$PkgPath)
    $raw = Get-Content $PkgPath -Raw
    $old = Get-ElectronVersion $PkgPath
    $new = Bump-SemverPatch $old
    $raw = $raw -replace ('"version"\s*:\s*"' + [regex]::Escape($old) + '"'), ('"version": "' + $new + '"')
    Set-Content -Path $PkgPath -Value $raw -NoNewline -Encoding UTF8
    Write-OK "Electron package.json: $old -> $new"
    return $new
}

function Invoke-BumpAndroidVersion {
    param([string]$GradlePath)
    $raw = Get-Content $GradlePath -Raw
    if ($raw -notmatch 'versionCode\s*=\s*(\d+)') { throw "versionCode not found in build.gradle.kts" }
    $oldCode = [int]$Matches[1]
    $newCode = $oldCode + 1
    $raw = $raw -replace ('versionCode\s*=\s*' + $oldCode), ('versionCode = ' + $newCode)
    if ($raw -notmatch 'versionName\s*=\s*"([^"]+)"') { throw "versionName not found in build.gradle.kts" }
    $old = $Matches[1]
    $new = Bump-SemverPatch $old
    $raw = $raw -replace ('versionName\s*=\s*"' + [regex]::Escape($old) + '"'), ('versionName = "' + $new + '"')
    Set-Content -Path $GradlePath -Value $raw -NoNewline -Encoding UTF8
    Write-OK "Android build.gradle.kts: $old (code $oldCode) -> $new (code $newCode)"
    return $new
}

function Update-ClientVersionsManifest {
    param(
        [string]$VersionsFile,
        [string]$ElectronVersion = $null,
        [string]$AndroidVersion = $null
    )
    if (Test-Path $VersionsFile) {
        $manifest = Get-Content $VersionsFile -Raw | ConvertFrom-Json
    } else {
        $manifest = [PSCustomObject]@{ version = ''; clients = [PSCustomObject]@{} }
    }
    if ($ElectronVersion) {
        $manifest.version = $ElectronVersion
        foreach ($key in @('windows', 'linux_appimage', 'linux_deb')) {
            if (-not $manifest.clients.$key) {
                $manifest.clients | Add-Member -NotePropertyName $key -NotePropertyValue ([PSCustomObject]@{}) -Force
            }
            $entry = $manifest.clients.$key
            $entry.version = $ElectronVersion
            if (-not $entry.filename) {
                $fn = switch ($key) {
                    'windows'        { 'AISignX-Player-Setup.exe' }
                    'linux_appimage' { 'AISignX-Player.AppImage' }
                    'linux_deb'      { 'AISignX-Player.deb' }
                }
                $entry | Add-Member -NotePropertyName filename -NotePropertyValue $fn -Force
                $entry | Add-Member -NotePropertyName url -NotePropertyValue "/static/clients/$fn" -Force
            }
        }
    }
    if ($AndroidVersion) {
        if (-not $manifest.clients.android) {
            $manifest.clients | Add-Member -NotePropertyName android -NotePropertyValue ([PSCustomObject]@{}) -Force
        }
        $manifest.clients.android.version = $AndroidVersion
        if (-not $manifest.clients.android.filename) {
            $manifest.clients.android | Add-Member -NotePropertyName filename -NotePropertyValue 'AISignX-Player.apk' -Force
            $manifest.clients.android | Add-Member -NotePropertyName url -NotePropertyValue '/static/clients/AISignX-Player.apk' -Force
        }
    }
    if (-not $manifest.version -and $ElectronVersion) { $manifest.version = $ElectronVersion }
    $manifest | ConvertTo-Json -Depth 5 | Set-Content $VersionsFile -Encoding UTF8
}

if ($Help) { Show-BuildHelp; exit 0 }

$buildElectron = $Electron.IsPresent -or (-not $Electron.IsPresent -and -not $Android.IsPresent)
$buildAndroid  = $Android.IsPresent  -or (-not $Electron.IsPresent -and -not $Android.IsPresent)
$doBump        = -not $NoBump.IsPresent
$doBuild       = -not $BumpOnly.IsPresent

$root         = Split-Path -Parent $MyInvocation.MyCommand.Path
$electronDir  = Join-Path $root "clients\electron-client"
$androidDir   = Join-Path $root "clients\android-client"
$outputDir    = Join-Path $root "server\static\clients"

Write-Header "AISignX Client Builder (Windows)"
Write-Host "  Targets: $(@(
    $(if ($buildElectron) { 'Electron' })
    $(if ($buildAndroid) { 'Android' })
) -join ', ')" -ForegroundColor Gray
Write-Host "  Bump versions: $(if ($doBump) { 'yes' } else { 'no' })  |  Compile: $(if ($doBuild) { 'yes' } else { 'no (BumpOnly)' })" -ForegroundColor Gray

$pkgPath    = Join-Path $electronDir "package.json"
$gradlePath = Join-Path $androidDir "app\build.gradle.kts"
$versionsFile = Join-Path $outputDir "client_versions.json"

if ($doBump) {
    Write-Header "Bumping client versions"
    if ($buildElectron) { [void](Invoke-BumpElectronVersion $pkgPath) }
    else { Write-Skip "Electron version bump skipped (-Android only)." }
    if ($buildAndroid) { [void](Invoke-BumpAndroidVersion $gradlePath) }
    else { Write-Skip "Android version bump skipped (-Electron only)." }
}

if ($BumpOnly) {
    Write-Header "Updating client_versions.json"
    $ev = if ($buildElectron) { Get-ElectronVersion $pkgPath } else { $null }
    $av = if ($buildAndroid)  { Get-AndroidVersion $gradlePath } else { $null }
    Update-ClientVersionsManifest -VersionsFile $versionsFile -ElectronVersion $ev -AndroidVersion $av
    Write-OK "Manifest updated."
    exit 0
}

# --- Refresh PATH from system registry (picks up winget/installer changes) ---
Write-Step "Refreshing PATH from system environment..."
$machinePath = [System.Environment]::GetEnvironmentVariable("PATH", "Machine")
$userPath    = [System.Environment]::GetEnvironmentVariable("PATH", "User")
$env:PATH    = "$machinePath;$userPath"

# --- Find node.exe wherever it lives if not already on PATH ---
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    Write-Step "node not on PATH - searching common install locations..."
    $nodeCandidates = @(
        "$env:ProgramFiles\nodejs",
        "${env:ProgramFiles(x86)}\nodejs",
        "$env:ProgramFiles\OpenJS\nodejs",
        "$env:LOCALAPPDATA\Programs\nodejs",
        "$env:APPDATA\nvm\current",
        "C:\nodejs",
        "C:\Program Files\nodejs",
        "C:\Program Files (x86)\nodejs"
    )
    # Also check every directory already on the machine PATH for node.exe
    ($machinePath + ";" + $userPath).Split(";") | Where-Object { $_ -ne "" } | ForEach-Object {
        if (Test-Path "$_\node.exe") { $nodeCandidates += $_ }
    }
    # Broad search under Program Files if still not found
    $found = $null
    foreach ($c in $nodeCandidates) {
        if ($c -and (Test-Path "$c\node.exe")) { $found = $c; break }
    }
    if (-not $found) {
        # Last resort - search Program Files dirs up to 2 levels deep
        foreach ($base in @($env:ProgramFiles, "${env:ProgramFiles(x86)}", $env:LOCALAPPDATA)) {
            if (-not $base) { continue }
            $hit = Get-ChildItem -Path $base -Recurse -Depth 2 -Filter "node.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($hit) { $found = $hit.DirectoryName; break }
        }
    }
    if ($found) {
        Write-OK "Found node.exe at: $found"
        $env:PATH = "$found;$env:PATH"
        [System.Environment]::SetEnvironmentVariable("PATH", "$found;" + [System.Environment]::GetEnvironmentVariable("PATH","Machine"), "Machine")
    }
}

# --- Check Node.js (Electron builds only) ---
if ($buildElectron) {
    Write-Step "Checking Node.js..."
    $nodePath = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodePath) {
        Write-Host ""
        Write-Host "[FAIL] node.exe could not be found on this machine." -ForegroundColor Red
        Write-Host ""
        Write-Host "  Fix options:" -ForegroundColor White
        Write-Host "    1. Run server\install_build_prereqs_windows.ps1 as Administrator" -ForegroundColor Yellow
        Write-Host "    2. Download Node.js manually from https://nodejs.org and install it" -ForegroundColor Yellow
        Write-Host "    3. After installing, open a NEW PowerShell window and re-run this script" -ForegroundColor Yellow
        Write-Host ""
        exit 1
    }
    $nodeVer = & node --version 2>&1
    Write-OK "Node.js $nodeVer  ($($nodePath.Source))"
    $npmVer = & npm --version 2>&1
    Write-OK "npm $npmVer"
}

# --- Ensure output directory ---
if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir | Out-Null
}

# =====================================================================
# ELECTRON - Windows (+ copy Linux artifacts from dist if present)
# =====================================================================
if ($buildElectron) {
Write-Header "Building Electron Client (Windows)"
Set-Location $electronDir

Write-Step "Installing npm dependencies..."
npm install --silent
Write-OK "npm install complete."

Write-Step "Building Windows installer (.exe)..."
npm run build:win
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Electron build failed. Check output above."
    Set-Location $root
    exit 1
}
Write-OK "Electron build complete. Artifacts in clients\electron-client\dist\"

# --- Copy Windows installer ---
Write-Step "Copying Windows installer to static/clients/..."
# Find the NSIS installer — it's the .exe that is NOT a .blockmap and NOT inside win-unpacked
$winExe = Get-ChildItem "$electronDir\dist" -Filter "*.exe" |
    Where-Object { $_.Name -notlike "*.blockmap" -and $_.DirectoryName -notlike "*win-unpacked*" } |
    Sort-Object Length -Descending |
    Select-Object -First 1
if ($winExe) {
    Copy-Item $winExe.FullName (Join-Path $outputDir "AISignX-Player-Setup.exe") -Force
    Write-OK "Copied: $($winExe.Name) -> AISignX-Player-Setup.exe"
} else {
    Write-Skip "Windows .exe not found in dist - skipping."
}

# --- Copy Linux AppImage ---
Write-Step "Copying Linux AppImage to static/clients/..."
$appImage = Get-ChildItem "$electronDir\dist" -Filter "*.AppImage" | Select-Object -First 1
if ($appImage) {
    Copy-Item $appImage.FullName (Join-Path $outputDir "AISignX-Player.AppImage") -Force
    Write-OK "Copied: AISignX-Player.AppImage"
} else {
    Write-Skip "Linux .AppImage not found in dist - skipping (cross-compile may not produce this on Windows)."
}

# --- Copy Linux .deb ---
$deb = Get-ChildItem "$electronDir\dist" -Filter "*.deb" | Select-Object -First 1
if ($deb) {
    Copy-Item $deb.FullName (Join-Path $outputDir "AISignX-Player.deb") -Force
    Write-OK "Copied: AISignX-Player.deb"
} else {
    Write-Skip "Linux .deb not found in dist - skipping."
}

Set-Location $root
} else {
    Write-Skip "Electron build skipped (-Android only)."
}

# =====================================================================
# ANDROID
# =====================================================================
if ($buildAndroid) {
Write-Header "Building Android Client"

$gradlew = Join-Path $androidDir "gradlew.bat"
if (-not (Test-Path $gradlew)) {
    Write-Fail "gradlew.bat not found at $gradlew. Run 'gradle wrapper' inside android-client/ first."
    exit 1
}

if (-not $env:JAVA_HOME) {
    Write-Fail "JAVA_HOME is not set. Set it to your JDK 17 installation path and re-run."
    exit 1
}
Write-OK "JAVA_HOME: $env:JAVA_HOME"

Set-Location $androidDir
Write-Step "Running Gradle assembleDebug (debug APK - no signing needed)..."
& $gradlew assembleDebug --quiet
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Android build failed. Check output above."
    Set-Location $root
    exit 1
}
Write-OK "Android build complete."

Write-Step "Copying APK to static/clients/..."
$apk = Get-ChildItem "$androidDir\app\build\outputs\apk\debug" -Filter "*.apk" | Select-Object -First 1
if ($apk) {
    Copy-Item $apk.FullName (Join-Path $outputDir "AISignX-Player.apk") -Force
    Write-OK "Copied: AISignX-Player.apk"
} else {
    Write-Fail "APK not found in expected location."
    Set-Location $root
    exit 1
}

Set-Location $root
} else {
    Write-Skip "Android build skipped (-Electron only)."
}

# =====================================================================
# Update client_versions.json
# =====================================================================
Write-Header "Updating client_versions.json"
$ev = if ($buildElectron) { Get-ElectronVersion $pkgPath } else { $null }
$av = if ($buildAndroid)  { Get-AndroidVersion $gradlePath } else { $null }
Update-ClientVersionsManifest -VersionsFile $versionsFile -ElectronVersion $ev -AndroidVersion $av
if ($ev) { Write-OK "Manifest — windows/linux: $ev" }
if ($av) { Write-OK "Manifest — android: $av" }

# =====================================================================
# Done
# =====================================================================
Write-Header "Build complete"
Write-Host ""
Write-Host "  Outputs in server\static\clients\:" -ForegroundColor White
Get-ChildItem $outputDir | Where-Object { $_.Extension -match "exe|AppImage|deb|apk" } | ForEach-Object {
    $size = [math]::Round($_.Length / 1MB, 1)
    Write-Host ("    {0,-40} {1} MB" -f $_.Name, $size) -ForegroundColor Green
}
Write-Host ""
Write-Host "  The server will serve these from the Downloads page." -ForegroundColor Gray
if ($doBump) {
    Write-Host "  Versions were bumped for selected targets at the start of this script." -ForegroundColor Gray
} else {
    Write-Host "  Version bump was skipped (-NoBump). Manifest reflects current source versions." -ForegroundColor Gray
}
Write-Host ""