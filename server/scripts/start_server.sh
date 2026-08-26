#!/usr/bin/env bash
# Start AISignX (Waitress) + Caddy HTTPS in one step.
# Usage (from server/):  chmod +x scripts/start_server.sh && ./scripts/start_server.sh
#
# Options:
#   --hostname IP_OR_NAME   used if deploy/Caddyfile is missing
#   --app-only             skip Caddy
#   --foreground           block this shell on Caddy (debug only)
#   --background           same as default (returns to prompt)
#   --skip-https-setup     do not auto-run setup_https.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DEPLOY_DIR="$SERVER_DIR/deploy"
CADDYFILE="$DEPLOY_DIR/Caddyfile"
LOGS_DIR="$SERVER_DIR/logs"
APP_PID="$LOGS_DIR/aisignx-app.pid"
CADDY_PID="$LOGS_DIR/caddy.pid"
VENV_PY="$SERVER_DIR/.venv/bin/python"
HOSTNAME="${AISIGNX_HOSTNAME:-}"
APP_ONLY=0
FOREGROUND=0
SKIP_SETUP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --hostname) HOSTNAME="$2"; shift 2 ;;
        --app-only) APP_ONLY=1; shift ;;
        --foreground) FOREGROUND=1; shift ;;
        --background) shift ;;
        --skip-https-setup) SKIP_SETUP=1; shift ;;
        -h|--help)
            sed -n '2,12p' "$0"
            exit 0
            ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
done

step() { echo -e "\033[33m[*]\033[0m $*"; }
ok()   { echo -e "\033[32m[OK]\033[0m $*"; }
warn() { echo -e "\033[33m[!]\033[0m $*"; }

stop_pid_file() {
    local file="$1" label="$2"
    if [[ -f "$file" ]]; then
        local pid
        pid="$(cat "$file" 2>/dev/null || true)"
        if [[ "$pid" =~ ^[0-9]+$ ]]; then
            kill "$pid" 2>/dev/null || true
            ok "Stopped $label (PID $pid)"
        fi
        rm -f "$file"
    fi
}

find_caddy() {
    if command -v caddy &>/dev/null; then
        command -v caddy
        return
    fi
    if [[ -x "$SERVER_DIR/tools/caddy/caddy" ]]; then
        echo "$SERVER_DIR/tools/caddy/caddy"
        return
    fi
    return 1
}

public_host_from_caddyfile() {
    if [[ ! -f "$CADDYFILE" ]]; then
        echo ""
        return
    fi
    local line
    line="$(head -n 1 "$CADDYFILE")"
    if [[ "$line" =~ ^[[:space:]]*([^[:space:]{]+)[[:space:]]*\{ ]]; then
        echo "${BASH_REMATCH[1]}"
    fi
}

cd "$SERVER_DIR"

if [[ ! -x "$VENV_PY" ]]; then
    echo "[FAIL] Missing .venv — run install_linux.sh first." >&2
    exit 1
fi

if [[ ! -f config.py ]]; then
    host="${HOSTNAME:-localhost}"
    step "Creating config.py (https, hostname=$host)..."
    "$VENV_PY" generate_config.py --mode https --hostname "$host"
fi

step "Ensuring waitress is installed..."
"$VENV_PY" -m pip install -q waitress

if [[ "$APP_ONLY" -eq 0 && ! -f "$CADDYFILE" ]]; then
    if [[ "$SKIP_SETUP" -eq 1 ]]; then
        echo "[FAIL] Missing $CADDYFILE — run scripts/setup_https.sh" >&2
        exit 1
    fi
    host="${HOSTNAME:-localhost}"
    step "Creating Caddyfile (setup_https.sh --hostname $host)..."
    chmod +x "$SCRIPT_DIR/setup_https.sh"
    AISIGNX_NONINTERACTIVE=1 "$SCRIPT_DIR/setup_https.sh" --hostname "$host"
fi

PUBLIC_HOST="$(public_host_from_caddyfile)"
[[ -z "$PUBLIC_HOST" ]] && PUBLIC_HOST="${HOSTNAME:-localhost}"
PUBLIC_URL="https://${PUBLIC_HOST}/"

mkdir -p "$LOGS_DIR"
stop_pid_file "$APP_PID" "AISignX"
stop_pid_file "$CADDY_PID" "Caddy"

step "Starting AISignX on http://127.0.0.1:5000..."
nohup "$VENV_PY" -m waitress --host=127.0.0.1 --port=5000 app:app \
    >>"$LOGS_DIR/app.out.log" 2>>"$LOGS_DIR/app.err.log" &
echo $! >"$APP_PID"
sleep 2
ok "AISignX started (PID $(cat "$APP_PID")) — logs: $LOGS_DIR/app.out.log"

if ! curl -fsS "http://127.0.0.1:5000/static/sw.js" >/dev/null 2>&1; then
    warn "AISignX not responding yet — check $LOGS_DIR/app.err.log"
fi

if [[ "$APP_ONLY" -eq 1 ]]; then
    echo ""
    ok "App-only mode. Admin (local): http://127.0.0.1:5000/"
    echo "Stop: ./scripts/stop_server.sh"
    exit 0
fi

CADDY_BIN="$(find_caddy)" || {
    echo "[FAIL] Caddy not found. Run: ./scripts/setup_https.sh --hostname $PUBLIC_HOST" >&2
    exit 1
}

if [[ "$FOREGROUND" -eq 1 ]]; then
    echo ""
    ok "Serving $PUBLIC_URL (foreground — Ctrl+C stops Caddy only)"
    echo ""
    trap 'stop_pid_file "$APP_PID" AISignX; stop_pid_file "$CADDY_PID" Caddy; exit 0' INT TERM
    if [[ "$(id -u)" -ne 0 ]]; then
        sudo "$CADDY_BIN" run --config "$CADDYFILE"
    else
        "$CADDY_BIN" run --config "$CADDYFILE"
    fi
    stop_pid_file "$APP_PID" "AISignX"
    exit 0
fi

step "Starting Caddy (HTTPS on :443)..."
if [[ "$(id -u)" -ne 0 ]]; then
    sudo bash -c "nohup '$CADDY_BIN' run --config '$CADDYFILE' >>'$LOGS_DIR/caddy.out.log' 2>>'$LOGS_DIR/caddy.err.log' & echo \$! > '$CADDY_PID'"
else
    nohup "$CADDY_BIN" run --config "$CADDYFILE" >>"$LOGS_DIR/caddy.out.log" 2>>"$LOGS_DIR/caddy.err.log" &
    echo $! >"$CADDY_PID"
fi
ok "Caddy started — logs: $LOGS_DIR/caddy.out.log"

echo ""
ok "AISignX is running (shell returned to prompt)."
echo "  Admin UI: $PUBLIC_URL"
echo "  Stop:     ./scripts/stop_server.sh"
echo "  Restart:  ./scripts/restart_server.sh"
echo ""
