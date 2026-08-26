# AISignX - Windows Install Script
# Run from the AISignX project directory:
#   powershell -ExecutionPolicy Bypass -File install_windows.ps1

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Header($msg) {
    Write-Host ""
    Write-Host "==================================================" -ForegroundColor Cyan
    Write-Host "  $msg" -ForegroundColor Cyan
    Write-Host "==================================================" -ForegroundColor Cyan
}

function Write-Step($msg) {
    Write-Host "[*] $msg" -ForegroundColor Yellow
}

function Write-OK($msg) {
    Write-Host "[OK] $msg" -ForegroundColor Green
}

function Write-Fail($msg) {
    Write-Host "[FAIL] $msg" -ForegroundColor Red
}

Write-Header "AISignX Installer for Windows"

. (Join-Path $PSScriptRoot 'scripts\caddy_lib.ps1')

# --- 1. Check Python ---
Write-Step "Checking Python version..."
try {
    $pyver = python --version 2>&1
    Write-OK "Found $pyver"
} catch {
    Write-Fail "Python not found. Install Python 3.10+ from https://python.org and re-run."
    exit 1
}

# --- 2. Create virtual environment (.venv preferred; existing venv/ is OK) ---
Write-Step "Creating virtual environment (.venv)..."
$venvPy = Resolve-AisignxPython
if ($venvPy) {
    Write-OK "Virtual environment already exists - skipping."
} else {
    python -m venv .venv
    $venvPy = Resolve-AisignxPython
    if (-not $venvPy) {
        Write-Fail "Could not create .venv. Run: python -m venv .venv"
        exit 1
    }
    Write-OK "Virtual environment created."
}

# --- 3. Activate virtual environment ---
Write-Step "Activating virtual environment..."
$venvDir = Split-Path (Split-Path $venvPy -Parent) -Parent
& (Join-Path $venvDir 'Scripts\Activate.ps1')
Write-OK "Virtual environment activated."

# --- 4. Install dependencies ---
Write-Step "Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
Write-OK "Dependencies installed."

# --- 5. Generate config (HTTPS default) ---
Write-Step "Server setup (HTTPS default)..."
$Hostname = $env:AISIGNX_HOSTNAME
if (-not $Hostname) {
    $Hostname = $env:COMPUTERNAME
    if (-not $Hostname) { $Hostname = "localhost" }
}
if (Test-Path "config.py") {
    Write-OK "config.py already exists - skipping. Run: python generate_config.py --show"
} else {
    python generate_config.py --mode https --hostname $Hostname
    Write-OK "config.py created for HTTPS hostname $Hostname."
}

if ($env:AISIGNX_SKIP_HTTPS_SETUP -ne "1") {
    Write-Step "HTTPS reverse proxy (Caddy)..."
    $setupScript = Join-Path $PSScriptRoot "scripts\setup_https.ps1"
    if (Test-Path $setupScript) {
        try {
            & $setupScript -Hostname $Hostname -NonInteractive
        } catch {
            Write-Host "[!] Caddy setup skipped or failed - run: .\scripts\setup_https.ps1 -Hostname $Hostname" -ForegroundColor Yellow
        }
    }
}

# --- 6. Run database migration ---
Write-Step "Running database migrations..."
python migration.py
Write-OK "Database ready."

# --- 7. Create uploads directory ---
Write-Step "Ensuring uploads directory exists..."
if (-not (Test-Path "uploads")) {
    New-Item -ItemType Directory -Path "uploads" | Out-Null
}
Write-OK "uploads/ directory ready."

# --- 8. Done ---
Write-Header "Installation Complete!"
Write-Host ""
Write-Host "  Start AISignX + HTTPS (one command):" -ForegroundColor White
Write-Host "    .\scripts\start_server.ps1 -Hostname $Hostname" -ForegroundColor Green
Write-Host "    .\scripts\stop_server.ps1" -ForegroundColor Green
Write-Host "    .\scripts\restart_server.ps1 -Hostname $Hostname" -ForegroundColor Green
Write-Host "  Open admin UI:" -ForegroundColor White
Write-Host "    https://$Hostname/" -ForegroundColor Green
Write-Host ""
Write-Host "  See docs\HTTPS_SETUP.md and docs\GETTING_STARTED.md" -ForegroundColor Gray
Write-Host ""