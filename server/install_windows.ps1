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

# --- 1. Check Python ---
Write-Step "Checking Python version..."
try {
    $pyver = python --version 2>&1
    Write-OK "Found $pyver"
} catch {
    Write-Fail "Python not found. Install Python 3.10+ from https://python.org and re-run."
    exit 1
}

# --- 2. Create virtual environment ---
Write-Step "Creating virtual environment (.venv)..."
if (Test-Path ".venv") {
    Write-OK "Virtual environment already exists - skipping."
} else {
    python -m venv .venv
    Write-OK "Virtual environment created."
}

# --- 3. Activate virtual environment ---
Write-Step "Activating virtual environment..."
& ".venv\Scripts\Activate.ps1"
Write-OK "Virtual environment activated."

# --- 4. Install dependencies ---
Write-Step "Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
Write-OK "Dependencies installed."

# --- 5. Generate config ---
Write-Step "Server setup (HTTP vs HTTPS)..."
if (Test-Path "config.py") {
    Write-OK "config.py already exists - skipping. Run: python generate_config.py --show"
} else {
    python generate_config.py --mode http
    Write-OK "config.py created (direct HTTP mode). Run: python generate_config.py --interactive --force to change."
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
Write-Host "  To start the server:" -ForegroundColor White
Write-Host "    .venv\Scripts\Activate.ps1" -ForegroundColor Green
Write-Host "    python app.py" -ForegroundColor Green
Write-Host ""
Write-Host "  Then open your browser at:" -ForegroundColor White
Write-Host "    http://localhost:5000" -ForegroundColor Green
Write-Host ""
Write-Host "  See docs\GETTING_STARTED.md in this server folder for next steps." -ForegroundColor Gray
Write-Host ""