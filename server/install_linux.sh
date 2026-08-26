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

# --- 6. Generate config (HTTPS default) ---
step "Server setup (HTTPS default)..."
HOSTNAME="${AISIGNX_HOSTNAME:-}"
if [ -z "$HOSTNAME" ]; then
    HOSTNAME="$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo localhost)"
    if [[ "$HOSTNAME" == *.* ]]; then
        HOSTNAME="${HOSTNAME%%.*}"
    fi
    [ -z "$HOSTNAME" ] || [ "$HOSTNAME" = "(none)" ] && HOSTNAME="localhost"
fi
if [ -f "config.py" ]; then
    ok "config.py already exists - skipping. Run: python generate_config.py --show"
else
    python generate_config.py --mode https --hostname "$HOSTNAME"
    ok "config.py created (HTTPS mode, hostname=$HOSTNAME)."
fi

# --- 6b. Caddy / TLS (optional but recommended) ---
if [ "${AISIGNX_SKIP_HTTPS_SETUP:-}" != "1" ]; then
    step "HTTPS reverse proxy (Caddy)..."
    chmod +x scripts/setup_https.sh 2>/dev/null || true
    if [ -f "scripts/setup_https.sh" ]; then
        AISIGNX_NONINTERACTIVE=1 AISIGNX_HOSTNAME="$HOSTNAME" AISIGNX_EMAIL="${AISIGNX_EMAIL:-}" \
            ./scripts/setup_https.sh --hostname "$HOSTNAME" || \
            echo -e "${YELLOW}[!] Caddy setup skipped or failed — run: ./scripts/setup_https.sh --hostname $HOSTNAME${NC}"
    fi
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
echo -e "  Start AISignX + HTTPS (one command):"
echo -e "    ${GREEN}chmod +x scripts/*.sh && ./scripts/start_server.sh --hostname ${HOSTNAME}${NC}"
echo -e "  Open admin UI:"
echo -e "    ${GREEN}https://${HOSTNAME}/${NC}"
echo ""
echo -e "  See docs/HTTPS_SETUP.md and docs/GETTING_STARTED.md"
echo ""