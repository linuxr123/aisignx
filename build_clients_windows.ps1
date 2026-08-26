# AISignX - Build Clients (Windows)
# Run from the repo root:
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Help
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Electron
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Android
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Electron -NoBump
#   powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Android -Release
#
# Target switches (pick one or both; default = both):
#   -Electron     Windows installer (+ copy Linux artifacts from dist if present)
#   -Android       APK (debug unless -Release)
#   -Target All|Electron|Android   Same as switches above (optional positional: Electron)
#
# Other:
#   -NoBump        Skip version bump in package.json / build.gradle.kts
#   -BumpOnly      Bump versions + client_versions.json only (no compile)
#   -Release       Signed Android release APK (requires keystore.properties)
#   -Help          Show full usage

param(
    [switch]$Help,
    [Parameter(Position = 0)]
    [ValidateSet('All', 'Electron', 'Android', 'all', 'electron', 'android', '')]
    [string]$Target = '',
    [switch]$Electron,
    [switch]$Android,
    [switch]$NoBump,
    [switch]$BumpOnly,
    [switch]$Release
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

function Write-TextNoBom([string]$Path, [string]$Content) {
    $enc = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

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
  powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Help

Targets (build one or both; if none specified, builds BOTH):
  -Electron              Build Windows .exe installer (Electron)
  -Android               Build Android APK (debug keystore)
  -Target <name>         All | Electron | Android  (same as the switches above)
  <Electron|Android|All> Optional first positional argument (= -Target)

Shared options:
  -NoBump                Do not increment versions in source files
  -BumpOnly              Only bump versions and update client_versions.json
  -Help                  Show this help

Android-only:
  -Release               Signed release APK (needs clients\android-client\keystore.properties)

Examples:
  .\build_clients_windows.ps1                         # bump + build Electron + Android
  .\build_clients_windows.ps1 -Electron               # bump + Windows installer only
  .\build_clients_windows.ps1 -Android                # bump + debug APK only
  .\build_clients_windows.ps1 -Electron -NoBump       # rebuild installer, same version
  .\build_clients_windows.ps1 -Android -Release       # signed release APK
  .\build_clients_windows.ps1 -BumpOnly -Electron     # bump Electron version only
  .\build_clients_windows.ps1 -Target Android         # same as -Android

"@
}

function Show-BuildOptionsBanner {
    Write-Host ""
    Write-Host "  Options:" -ForegroundColor White
    Write-Host "    -Electron   -Android     (default: both if neither is set)" -ForegroundColor Gray
    Write-Host "    -Target All|Electron|Android" -ForegroundColor Gray
    Write-Host "    -NoBump  -BumpOnly  -Release (Android signed APK)  -Help" -ForegroundColor Gray
    Write-Host ""
}

function Resolve-BuildTargets {
    param(
        [string]$Target,
        [bool]$ElectronSwitch,
        [bool]$AndroidSwitch
    )
    if ($ElectronSwitch -and $AndroidSwitch) {
        return @{ Electron = $true; Android = $true }
    }
    if ($ElectronSwitch) {
        return @{ Electron = $true; Android = $false }
    }
    if ($AndroidSwitch) {
        return @{ Electron = $false; Android = $true }
    }
    $t = ($Target + '').Trim()
    if ($t -eq '') {
        return @{ Electron = $true; Android = $true }
    }
    switch ($t.ToLowerInvariant()) {
        'electron' { return @{ Electron = $true; Android = $false } }
        'android'  { return @{ Electron = $false; Android = $true } }
        'all'      { return @{ Electron = $true; Android = $true } }
        default    { throw "Unknown target '$t'. Use All, Electron, or Android (or -Help)." }
    }
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
    Write-TextNoBom $PkgPath $raw
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
    Write-TextNoBom $GradlePath $raw
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
    Write-TextNoBom $VersionsFile ($manifest | ConvertTo-Json -Depth 5)
}

if ($Help) { Show-BuildHelp; exit 0 }

$targets = Resolve-BuildTargets -Target $Target -ElectronSwitch $Electron.IsPresent -AndroidSwitch $Android.IsPresent
$buildElectron = $targets.Electron
$buildAndroid  = $targets.Android
$doBump        = -not $NoBump.IsPresent
$doBuild       = -not $BumpOnly.IsPresent

if ($Release.IsPresent -and -not $buildAndroid) {
    Write-Fail "-Release applies to Android only. Add -Android or use -Target Android."
    exit 1
}

$root         = Split-Path -Parent $MyInvocation.MyCommand.Path
$electronDir  = Join-Path $root "clients\electron-client"
$androidDir   = Join-Path $root "clients\android-client"
$outputDir    = Join-Path $root "server\static\clients"

Write-Header "AISignX Client Builder (Windows)"
Show-BuildOptionsBanner
$targetNames = @()
if ($buildElectron) { $targetNames += 'Electron' }
if ($buildAndroid)  { $targetNames += 'Android' }
Write-Host "  Selected: $($targetNames -join ', ')" -ForegroundColor Cyan
Write-Host "  Bump versions: $(if ($doBump) { 'yes' } else { 'no' })  |  Compile: $(if ($doBuild) { 'yes' } else { 'no (BumpOnly)' })" -ForegroundColor Gray
if ($Release.IsPresent) {
    Write-Host "  Android build: signed RELEASE APK" -ForegroundColor Gray
}

$pkgPath    = Join-Path $electronDir "package.json"
$gradlePath = Join-Path $androidDir "app\build.gradle.kts"
$versionsFile = Join-Path $outputDir "client_versions.json"

if ($doBump) {
    Write-Header "Bumping client versions"
    if ($buildElectron) { [void](Invoke-BumpElectronVersion $pkgPath) }
    else { Write-Skip "Electron version bump skipped (not in selected targets)." }
    if ($buildAndroid) { [void](Invoke-BumpAndroidVersion $gradlePath) }
    else { Write-Skip "Android version bump skipped (not in selected targets)." }
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
if ($buildElectron) {
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
    ($machinePath + ";" + $userPath).Split(";") | Where-Object { $_ -ne "" } | ForEach-Object {
        if (Test-Path "$_\node.exe") { $nodeCandidates += $_ }
    }
    $found = $null
    foreach ($c in $nodeCandidates) {
        if ($c -and (Test-Path "$c\node.exe")) { $found = $c; break }
    }
    if (-not $found) {
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

    Write-Step "Checking Node.js..."
    $nodePath = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodePath) {
        Write-Host ""
        Write-Fail "node.exe could not be found (required for -Electron)."
        Write-Host ""
        Write-Host "  Fix options:" -ForegroundColor White
        Write-Host "    1. Run server\install_build_prereqs_windows.ps1 as Administrator" -ForegroundColor Yellow
        Write-Host "    2. Install Node.js from https://nodejs.org" -ForegroundColor Yellow
        Write-Host "    3. Open a NEW PowerShell window and re-run this script" -ForegroundColor Yellow
        Write-Host "    Or build Android only:  .\build_clients_windows.ps1 -Android" -ForegroundColor Yellow
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
Write-Host "  (prebuild:win stops any running player and clears dist/ to avoid app.asar locks)" -ForegroundColor DarkGray
npm run build:win
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Electron build failed. Check output above."
    Set-Location $root
    exit 1
}
$electronDist = Join-Path $electronDir "dist-build"
Write-OK "Electron build complete. Artifacts in clients\electron-client\dist-build\"

Write-Step "Copying Windows installer to static/clients/..."
$winExe = Get-ChildItem "$electronDist" -Filter "*.exe" |
    Where-Object { $_.Name -notlike "*.blockmap" -and $_.DirectoryName -notlike "*win-unpacked*" } |
    Sort-Object Length -Descending |
    Select-Object -First 1
if ($winExe) {
    Copy-Item $winExe.FullName (Join-Path $outputDir "AISignX-Player-Setup.exe") -Force
    Write-OK "Copied: $($winExe.Name) -> AISignX-Player-Setup.exe"
} else {
    Write-Skip "Windows .exe not found in dist - skipping."
}

Write-Step "Copying Linux AppImage to static/clients/..."
$appImage = Get-ChildItem "$electronDist" -Filter "*.AppImage" | Select-Object -First 1
if ($appImage) {
    Copy-Item $appImage.FullName (Join-Path $outputDir "AISignX-Player.AppImage") -Force
    Write-OK "Copied: AISignX-Player.AppImage"
} else {
    Write-Skip "Linux .AppImage not found in dist - skipping (cross-compile may not produce this on Windows)."
}

$deb = Get-ChildItem "$electronDist" -Filter "*.deb" | Select-Object -First 1
if ($deb) {
    Copy-Item $deb.FullName (Join-Path $outputDir "AISignX-Player.deb") -Force
    Write-OK "Copied: AISignX-Player.deb"
} else {
    Write-Skip "Linux .deb not found in dist - skipping."
}

Set-Location $root
} else {
    Write-Skip "Electron build skipped (use -Electron or -Target Electron to include it)."
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
    Write-Fail "JAVA_HOME is not set (required for -Android). Set JDK 17 and re-run."
    exit 1
}
Write-OK "JAVA_HOME: $env:JAVA_HOME"

$keystorePropsPath = Join-Path $androidDir "keystore.properties"
$useRelease = $Release.IsPresent
if ($useRelease -and -not (Test-Path $keystorePropsPath)) {
    Write-Fail "-Release requires clients\android-client\keystore.properties (see keystore.properties.example)."
    Write-Host "  A release APK MUST be signed with the SAME keystore as the installed app, or" -ForegroundColor Yellow
    Write-Host "  Android will reject the update (signature mismatch)." -ForegroundColor Yellow
    Set-Location $root
    exit 1
}

Set-Location $androidDir
if ($useRelease) {
    Write-Step "Running Gradle assembleRelease (signed with keystore.properties)..."
    & $gradlew assembleRelease --quiet
} else {
    Write-Step "Running Gradle assembleDebug (debug APK - debug keystore)..."
    Write-Host "  NOTE: debug APKs are signed with this machine's debug key. Devices first" -ForegroundColor DarkGray
    Write-Host "  installed from a different build will REJECT this as an update. Use -Release" -ForegroundColor DarkGray
    Write-Host "  with a stable keystore for fleets that must auto-update." -ForegroundColor DarkGray
    & $gradlew assembleDebug --quiet
}
if ($LASTEXITCODE -ne 0) {
    Write-Fail "Android build failed. Check output above."
    Set-Location $root
    exit 1
}
Write-OK "Android build complete."

Write-Step "Copying APK to static/clients/..."
$apkDir = if ($useRelease) { "$androidDir\app\build\outputs\apk\release" }
          else             { "$androidDir\app\build\outputs\apk\debug" }
$apk = Get-ChildItem $apkDir -Filter "*.apk" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notlike "*unsigned*" } |
    Select-Object -First 1
if ($apk) {
    Copy-Item $apk.FullName (Join-Path $outputDir "AISignX-Player.apk") -Force
    Write-OK "Copied: $($apk.Name) -> AISignX-Player.apk"
} else {
    Write-Fail "APK not found in $apkDir (release build may have produced only an unsigned APK; check signing config)."
    Set-Location $root
    exit 1
}

Set-Location $root
} else {
    Write-Skip "Android build skipped (use -Android or -Target Android to include it)."
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
Write-Host "  Other targets: -Electron  -Android  (see -Help)" -ForegroundColor DarkGray
Write-Host ""
