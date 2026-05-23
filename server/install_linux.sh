#!/usr/bin/env bash
# AISignX - Linux / macOS Install Script
# Run from the AISignX project directory:
#   chmod +x install_linux.sh && ./install_linux.sh

set -euo pipefail

CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

header() { echo -e "\n${CYAN}==================================================\n  $1\n==================================================${NC}"; }
step()   { echo -e "${YELLOW}[*] $1${NC}"; }
ok()     { echo -e "${GREEN}[OK] $1${NC}"; }
fail()   { echo -e "${RED}[FAIL] $1${NC}"; exit 1; }

header "AISignX Installer for Linux / macOS"

# --- 1. Check Python ---
step "Checking Python version..."
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    fail "Python not found. Install Python 3.10+ and re-run."
fi
ok "Found $($PYTHON --version)"

# --- 2. Check pip ---
step "Checking pip..."
if ! $PYTHON -m pip --version &>/dev/null; then
    fail "pip not found. Install it with: sudo apt install python3-pip  (or equivalent)"
fi
ok "pip available."

# --- 3. Create virtual environment ---
step "Creating virtual environment (.venv)..."
if [ -d ".venv" ]; then
    ok "Virtual environment already exists - skipping."
else
    $PYTHON -m venv .venv
    ok "Virtual environment created."
fi

# --- 4. Activate virtual environment ---
step "Activating virtual environment..."
source .venv/bin/activate
ok "Virtual environment activated. ($(python --version))"

# --- 5. Install dependencies ---
step "Installing Python dependencies..."
pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet
ok "Dependencies installed."

# --- 6. Generate config ---
step "Generating config.py..."
if [ -f "config.py" ]; then
    ok "config.py already exists - skipping. Delete it to regenerate."
else
    python generate_config.py
    ok "config.py generated with a random SECRET_KEY."
fi

# --- 7. Run database migration ---
step "Running database migrations..."
python migration.py
ok "Database ready."

# --- 8. Create uploads directory ---
step "Ensuring uploads directory exists..."
mkdir -p uploads
ok "uploads/ directory ready."

# --- 9. Fix permissions (Linux only) ---
if [[ "$OSTYPE" == "linux-gnu"* ]]; then
    step "Setting uploads directory permissions..."
    chmod 755 uploads
    ok "Permissions set."
fi

# --- 10. Done ---
header "Installation Complete!"
echo ""
echo -e "  To start the server:"
echo -e "    ${GREEN}source .venv/bin/activate${NC}"
echo -e "    ${GREEN}python app.py${NC}"
echo ""
echo -e "  Or in one line:"
echo -e "    ${GREEN}source .venv/bin/activate && python app.py${NC}"
echo ""
echo -e "  Then open your browser at:"
echo -e "    ${GREEN}http://localhost:5000${NC}"
echo ""
echo -e "  See docs/GETTING_STARTED.md for next steps."
echo ""