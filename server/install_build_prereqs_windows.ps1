# AISignX - Build Prerequisites Installer (Windows)
# Installs Node.js 20 LTS, Eclipse Temurin JDK 17, Android SDK command-line tools
# Run as Administrator from any directory:
#   powershell -ExecutionPolicy Bypass -File install_build_prereqs_windows.ps1

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
function Write-Fail($msg) { Write-Host "[FAIL] $msg" -ForegroundColor Red; exit 1 }

# --- Must run as admin ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    Write-Fail "Please run this script as Administrator (right-click -> Run as administrator)."
}

Write-Header "AISignX Build Prerequisites Installer (Windows)"
Write-Host "  This will install:" -ForegroundColor White
Write-Host "    - Node.js 20 LTS  (Electron builds)" -ForegroundColor Gray
Write-Host "    - Eclipse Temurin JDK 17  (Android builds)" -ForegroundColor Gray
Write-Host "    - Android SDK command-line tools  (Android builds)" -ForegroundColor Gray
Write-Host ""

# =====================================================================
# 1. Node.js 20 LTS
# =====================================================================
Write-Header "Node.js 20 LTS"
if (Get-Command node -ErrorAction SilentlyContinue) {
    $v = node --version
    Write-Skip "Node.js already installed: $v"
} else {
    # Try winget first but do NOT trust its exit code — verify node.exe actually exists after
    Write-Step "Attempting install via winget..."
    try {
        winget install --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements --silent 2>&1 | Out-Null
    } catch {}

    # Reload PATH and probe common dirs
    $mp = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
    $up = [System.Environment]::GetEnvironmentVariable("PATH","User")
    $env:PATH = "$mp;$up"
    $nodeFound = $null
    foreach ($c in @("$env:ProgramFiles\nodejs","${env:ProgramFiles(x86)}\nodejs","$env:ProgramFiles\OpenJS\nodejs","$env:LOCALAPPDATA\Programs\nodejs")) {
        if (Test-Path "$c\node.exe") { $nodeFound = $c; break }
    }

    # winget didn't work — fall back to direct MSI download
    if (-not $nodeFound) {
        Write-Step "winget did not install Node.js. Downloading official Node.js 20 LTS MSI..."
        $msiUrl  = "https://nodejs.org/dist/v20.19.2/node-v20.19.2-x64.msi"
        $msiPath = "$env:TEMP\node-v20-x64.msi"
        Write-Step "Downloading from $msiUrl ..."
        Invoke-WebRequest -Uri $msiUrl -OutFile $msiPath -UseBasicParsing
        Write-OK "Downloaded."
        Write-Step "Installing (silent)..."
        Start-Process msiexec.exe -ArgumentList "/i `"$msiPath`" /qn /norestart ADDLOCAL=ALL" -Wait -NoNewWindow
        Remove-Item $msiPath -ErrorAction SilentlyContinue
        Write-OK "Node.js MSI installed."
    }

    # Reload PATH again after MSI install and locate node.exe
    $mp = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
    $up = [System.Environment]::GetEnvironmentVariable("PATH","User")
    $env:PATH = "$mp;$up"
    foreach ($c in @("$env:ProgramFiles\nodejs","${env:ProgramFiles(x86)}\nodejs","$env:ProgramFiles\OpenJS\nodejs","$env:LOCALAPPDATA\Programs\nodejs")) {
        if (Test-Path "$c\node.exe") { $nodeFound = $c; break }
    }

    if ($nodeFound) {
        Write-OK "Node.js found at: $nodeFound"
        # Ensure it is permanently on the system PATH
        $currentMachine = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
        if ($currentMachine -notlike "*$nodeFound*") {
            [System.Environment]::SetEnvironmentVariable("PATH", "$nodeFound;$currentMachine", "Machine")
            Write-OK "Added $nodeFound to system PATH."
        }
        $env:PATH = "$nodeFound;$env:PATH"
    } else {
        Write-Host "[FAIL] Node.js installation could not be verified." -ForegroundColor Red
        Write-Host "       Please install manually from https://nodejs.org/en/download" -ForegroundColor Yellow
        Write-Host "       Then re-run this script." -ForegroundColor Yellow
        exit 1
    }
}

# =====================================================================
# 2. Eclipse Temurin JDK 17
# =====================================================================
Write-Header "Eclipse Temurin JDK 17"
$javaOK = $false
$javaVerStr = ""
if (Get-Command java -ErrorAction SilentlyContinue) {
    # java -version always writes to stderr; suspend Stop behaviour just for this call
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $javaVerStr = (& java -version 2>&1) | Out-String
    $ErrorActionPreference = $prev
    if ($javaVerStr -match '17\.') { $javaOK = $true }
}
if ($javaOK) {
    Write-Skip "Java 17 already installed: $($javaVerStr.Trim().Split("`n")[0])"
} else {
    Write-Step "Installing Eclipse Temurin JDK 17 via winget..."
    $prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
    winget install --id EclipseAdoptium.Temurin.17.JDK --accept-source-agreements --accept-package-agreements --silent 2>&1 | Out-Null
    $ErrorActionPreference = $prev
    Write-OK "JDK 17 installed."
}

# =====================================================================
# 3. Android SDK command-line tools
# =====================================================================
Write-Header "Android SDK Command-Line Tools"

$androidHome = "$env:LOCALAPPDATA\Android\Sdk"
$cmdlineToolsDir = "$androidHome\cmdline-tools\latest\bin"
$sdkmanager = "$cmdlineToolsDir\sdkmanager.bat"

if (Test-Path $sdkmanager) {
    Write-Skip "Android SDK command-line tools already installed at $androidHome"
} else {
    Write-Step "Downloading Android SDK command-line tools..."
    $zipUrl  = "https://dl.google.com/android/repository/commandlinetools-win-11076708_latest.zip"
    $zipPath = "$env:TEMP\cmdline-tools.zip"
    $extract = "$env:TEMP\android-cmdline-tools"

    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing
    Write-OK "Downloaded."

    Write-Step "Extracting..."
    if (Test-Path $extract) { Remove-Item $extract -Recurse -Force }
    Expand-Archive -Path $zipPath -DestinationPath $extract
    Remove-Item $zipPath

    # Move into correct directory structure: $ANDROID_HOME/cmdline-tools/latest/
    $dest = "$androidHome\cmdline-tools\latest"
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    Copy-Item "$extract\cmdline-tools\*" -Destination $dest -Recurse -Force
    Remove-Item $extract -Recurse -Force
    Write-OK "Extracted to $dest"

    # --- Set ANDROID_HOME system env var ---
    Write-Step "Setting ANDROID_HOME system environment variable..."
    [System.Environment]::SetEnvironmentVariable("ANDROID_HOME", $androidHome, "Machine")
    $env:ANDROID_HOME = $androidHome
    Write-OK "ANDROID_HOME = $androidHome"

    # --- Accept licenses and install required SDK packages ---
    Write-Step "Installing Android SDK platform 34, build-tools, and platform-tools..."
    $jdk17 = Get-ChildItem "C:\Program Files\Eclipse Adoptium" -Filter "jdk-17*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($jdk17) {
        $env:JAVA_HOME = $jdk17.FullName
        [System.Environment]::SetEnvironmentVariable("JAVA_HOME", $jdk17.FullName, "Machine")
        Write-OK "JAVA_HOME = $($jdk17.FullName)"
    }

    $packages = "platform-tools", "platforms;android-34", "build-tools;34.0.0"
    foreach ($pkg in $packages) {
        Write-Step "  Installing $pkg ..."
        echo "y" | & $sdkmanager $pkg
    }
    Write-OK "Android SDK packages installed."
}

# =====================================================================
# 4. Set JAVA_HOME if not already set
# =====================================================================
Write-Header "Checking JAVA_HOME"
if (-not [System.Environment]::GetEnvironmentVariable("JAVA_HOME", "Machine")) {
    $jdk17 = Get-ChildItem "C:\Program Files\Eclipse Adoptium" -Filter "jdk-17*" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($jdk17) {
        [System.Environment]::SetEnvironmentVariable("JAVA_HOME", $jdk17.FullName, "Machine")
        Write-OK "JAVA_HOME set to $($jdk17.FullName)"
    } else {
        Write-Skip "JDK 17 path not found under Eclipse Adoptium - set JAVA_HOME manually if Android build fails."
    }
} else {
    Write-OK "JAVA_HOME already set: $([System.Environment]::GetEnvironmentVariable('JAVA_HOME','Machine'))"
}

# =====================================================================
# 5. Reload PATH in current session
# =====================================================================
Write-Header "Reloading PATH"
$machinePath = [System.Environment]::GetEnvironmentVariable("PATH","Machine")
$userPath    = [System.Environment]::GetEnvironmentVariable("PATH","User")
$env:PATH    = "$machinePath;$userPath"
Write-OK "PATH refreshed."

# =====================================================================
# Done
# =====================================================================
Write-Header "Prerequisites Installed!"
Write-Host ""
Write-Host "  Installed / verified:" -ForegroundColor White
$prev = $ErrorActionPreference; $ErrorActionPreference = "Continue"
try { Write-Host "    Node.js  : $( (& node --version 2>&1) | Out-String | ForEach-Object { $_.Trim() } )" -ForegroundColor Green } catch { Write-Host "    Node.js  : installed (open a new terminal to use it)" -ForegroundColor Yellow }
try { Write-Host "    npm      : $( (& npm --version 2>&1)  | Out-String | ForEach-Object { $_.Trim() } )" -ForegroundColor Green } catch {}
$ErrorActionPreference = $prev
Write-Host "    JAVA_HOME    : $([System.Environment]::GetEnvironmentVariable('JAVA_HOME','Machine'))" -ForegroundColor Green
Write-Host "    ANDROID_HOME : $([System.Environment]::GetEnvironmentVariable('ANDROID_HOME','Machine'))" -ForegroundColor Green
Write-Host ""
Write-Host "  IMPORTANT: Open a NEW PowerShell window before running build_clients_windows.ps1" -ForegroundColor Cyan
Write-Host "  The new window will pick up all updated PATH and environment variables." -ForegroundColor Cyan
Write-Host ""
Write-Host ""