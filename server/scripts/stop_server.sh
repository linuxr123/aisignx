#!/usr/bin/env bash
# Stop AISignX (Waitress) + Caddy — foreground and --background starts.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVER_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOGS_DIR="$SERVER_DIR/logs"
DEPLOY_DIR="$SERVER_DIR/deploy"
CADDYFILE="$DEPLOY_DIR/Caddyfile"

stop_pid() {
    local file="$1" label="$2"
    if [[ -f "$file" ]]; then
        local pid
        pid="$(cat "$file" 2>/dev/null | tr -d '[:space:]')"
        if [[ "$pid" =~ ^[0-9]+$ ]]; then
            kill -TERM "$pid" 2>/dev/null || sudo kill -TERM "$pid" 2>/dev/null || true
            sleep 0.5
            kill -KILL "$pid" 2>/dev/null || sudo kill -KILL "$pid" 2>/dev/null || true
            echo "[OK] Stopped $label (PID $pid)"
        fi
        rm -f "$file"
    fi
}

stop_port() {
    local port="$1"
    if command -v fuser &>/dev/null; then
        fuser -k "${port}/tcp" 2>/dev/null || true
    elif command -v lsof &>/dev/null; then
        lsof -ti ":$port" 2>/dev/null | xargs -r kill -9 2>/dev/null || true
    fi
}

echo "Stopping AISignX server processes..."
stop_pid "$LOGS_DIR/aisignx-app.pid" "AISignX"
stop_pid "$LOGS_DIR/caddy.pid" "Caddy"

# Waitress / app under this server directory
if command -v pgrep &>/dev/null; then
    pgrep -f "waitress.*${SERVER_DIR}" 2>/dev/null | xargs -r kill -TERM 2>/dev/null || true
    sleep 0.5
    pgrep -f "waitress.*${SERVER_DIR}" 2>/dev/null | xargs -r kill -KILL 2>/dev/null || true
fi

# Caddy using our config
if command -v pgrep &>/dev/null; then
    pgrep -f "caddy.*${CADDYFILE}" 2>/dev/null | xargs -r kill -TERM 2>/dev/null || true
    sleep 0.5
    pgrep -f "caddy.*${CADDYFILE}" 2>/dev/null | xargs -r kill -KILL 2>/dev/null || true
fi

pkill -f "caddy run --config ${CADDYFILE}" 2>/dev/null || true
pkill -x caddy 2>/dev/null || true

stop_port 5000
stop_port 443

echo "[OK] Stop complete."
