#!/usr/bin/env bash
# Install the AiSignX server as a service that starts with the computer (Linux with systemd, macOS).
#
#   sudo ./scripts/install-server.sh                 install, or upgrade an existing install
#   sudo ./scripts/install-server.sh --port 8080     listen on another port
#   sudo ./scripts/install-server.sh --uninstall     remove the service and program (keeps your data)
#   sudo ./scripts/install-server.sh --uninstall --purge   ... and delete the data too
#
# Run it from the AiSignX folder (the one that contains server/), or point --source at one.
# It is safe to run again: it upgrades in place, backs up the database first, and keeps your data.
# Docker users do not need this: see docs/docker.md.
set -euo pipefail

PORT=5000
HOST=0.0.0.0
YES=0
UNINSTALL=0
PURGE=0
START=1
OPEN_FIREWALL=0
SOURCE=""
PREFIX=""
DATA=""
SERVICE_USER=""
HTTPS=0   # --https: also run setup-nginx.sh at the end (nginx + HTTPS)

usage() { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?--port needs a number}"; shift 2 ;;
    --host) HOST="${2:?--host needs an address}"; shift 2 ;;
    --source) SOURCE="${2:?--source needs a folder}"; shift 2 ;;
    --prefix) PREFIX="${2:?--prefix needs a folder}"; shift 2 ;;
    --data-dir) DATA="${2:?--data-dir needs a folder}"; shift 2 ;;
    --user) SERVICE_USER="${2:?--user needs a name}"; shift 2 ;;
    --no-start) START=0; shift ;;
    --https) HTTPS=1; shift ;;
    --open-firewall) OPEN_FIREWALL=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --purge) PURGE=1; shift ;;
    -y|--yes) YES=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$1"; }
warn() { printf '    \033[33m%s\033[0m\n' "$1"; }
die()  { printf '\033[31mError: %s\033[0m\n' "$1" >&2; exit 1; }

[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || die "--port must be a number from 1 to 65535."

# --- Where we are -------------------------------------------------------------------------
OS="$(uname -s)"
case "$OS" in
  Linux)  [[ -d /run/systemd/system ]] || die "This Linux does not use systemd. See docs/installing-the-server.md for a manual setup, or use Docker."
          : "${PREFIX:=/opt/aisignx}"; : "${DATA:=/var/lib/aisignx}"; : "${SERVICE_USER:=aisignx}" ;;
  Darwin) : "${PREFIX:=/usr/local/aisignx}"; : "${DATA:=/Library/Application Support/AiSignX}"; : "${SERVICE_USER:=_aisignx}" ;;
  *) die "Unsupported system: $OS (this script is for Linux and macOS; Windows has install-server.ps1)." ;;
esac
[[ $EUID -eq 0 ]] || die "Run this as root: sudo $0 $*"

APP="$PREFIX/app"; VENV="$PREFIX/venv"
ENV_FILE="/etc/aisignx/aisignx.env"
[[ "$OS" == "Darwin" ]] && ENV_FILE="$DATA/aisignx.env"
UNIT="/etc/systemd/system/aisignx.service"
PLIST="/Library/LaunchDaemons/com.aisignx.server.plist"
LABEL="com.aisignx.server"
LOG_DIR="$DATA/logs"

service_stop() {
  if [[ "$OS" == "Linux" ]]; then systemctl stop aisignx 2>/dev/null || true
  else launchctl bootout "system/$LABEL" 2>/dev/null || launchctl unload "$PLIST" 2>/dev/null || true; fi
}

# --- Uninstall ----------------------------------------------------------------------------
if [[ $UNINSTALL -eq 1 ]]; then
  step "Removing the AiSignX service"
  service_stop
  if [[ "$OS" == "Linux" ]]; then
    systemctl disable aisignx 2>/dev/null || true
    rm -f "$UNIT"; systemctl daemon-reload || true
    rm -f "$ENV_FILE"; rmdir /etc/aisignx 2>/dev/null || true
  else
    rm -f "$PLIST"
  fi
  rm -rf "$APP" "$APP.prev" "$APP.new" "$VENV"; rmdir "$PREFIX" 2>/dev/null || true
  if [[ $PURGE -eq 1 ]]; then
    rm -rf "$DATA"
    if [[ "$OS" == "Linux" ]]; then userdel "$SERVICE_USER" 2>/dev/null || true
    else dscl . -delete "/Users/$SERVICE_USER" 2>/dev/null || true; fi
    ok "Removed the program, the service and all data."
  else
    ok "Removed the program and the service. Your data is still in $DATA (use --purge to delete it)."
  fi
  exit 0
fi

# --- Source -------------------------------------------------------------------------------
if [[ -z "$SOURCE" ]]; then SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; fi
[[ -f "$SOURCE/server/wsgi.py" && -f "$SOURCE/server/requirements.txt" ]] || die "$SOURCE does not look like an AiSignX folder (no server/wsgi.py)."
VERSION="$(sed -n 's/^__version__ *= *"\(.*\)"/\1/p' "$SOURCE/server/aisignx/__init__.py" | head -1)"
UPGRADE=0; [[ -d "$APP" ]] && UPGRADE=1
step "AiSignX ${VERSION:-?}: $([[ $UPGRADE -eq 1 ]] && echo "upgrading" || echo "installing") into $PREFIX (data in $DATA)"

# --- Package manager and Python -----------------------------------------------------------
PM=""
for c in apt-get dnf pacman zypper brew; do command -v "$c" >/dev/null 2>&1 && { PM="$c"; break; }; done
confirm() { [[ $YES -eq 1 ]] && return 0; read -r -p "    $1 [Y/n] " a; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }
pkg_install() {
  case "$PM" in
    apt-get) DEBIAN_FRONTEND=noninteractive apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
    dnf) dnf install -y "$@" ;;
    pacman) pacman -S --noconfirm "$@" ;;
    zypper) zypper --non-interactive install "$@" ;;
    brew) sudo -u "${SUDO_USER:-root}" brew install "$@" ;;
    *) return 1 ;;
  esac
}
find_python() {
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      command -v "$c"; return 0
    fi
  done
  return 1
}
step "Python 3.10 or newer"
PY="$(find_python || true)"
if [[ -z "$PY" ]]; then
  warn "Python 3.10+ was not found."
  if [[ "$OS" == "Linux" && -n "$PM" ]] && confirm "Install Python with $PM?"; then
    case "$PM" in apt-get) pkg_install python3 python3-venv python3-pip ;; *) pkg_install python3 python3-pip ;; esac
  elif [[ "$OS" == "Darwin" && "$PM" == "brew" ]] && confirm "Install Python with Homebrew?"; then
    pkg_install python@3.12
  fi
  PY="$(find_python || true)"
  [[ -n "$PY" ]] || die "Install Python 3.10 or newer (macOS: brew install python) and run this again."
fi
ok "Using $PY ($("$PY" --version 2>&1))"
"$PY" -c 'import venv, ensurepip' 2>/dev/null || { [[ "$PM" == "apt-get" ]] && pkg_install python3-venv python3-pip; "$PY" -c 'import venv, ensurepip' || die "Python's venv module is missing (Debian/Ubuntu: apt install python3-venv)."; }

# --- Optional tools -----------------------------------------------------------------------
step "ffmpeg (video length and thumbnails) and a browser (web page previews)"
if ! command -v ffmpeg >/dev/null 2>&1; then
  warn "ffmpeg is missing: videos upload without a length or thumbnail."
  [[ -n "$PM" ]] && confirm "Install ffmpeg?" && pkg_install ffmpeg || true
fi
command -v ffmpeg >/dev/null 2>&1 && ok "ffmpeg found" || warn "Continuing without ffmpeg (install it later and restart the service)."
if ! { command -v chromium >/dev/null 2>&1 || command -v chromium-browser >/dev/null 2>&1 || command -v google-chrome >/dev/null 2>&1 || [[ -d "/Applications/Google Chrome.app" || -d "/Applications/Microsoft Edge.app" ]]; }; then
  warn "No Chromium, Chrome or Edge: web pages get a title card instead of a screenshot."
  if [[ "$OS" == "Linux" && -n "$PM" ]] && confirm "Install Chromium?"; then
    case "$PM" in apt-get) pkg_install chromium || pkg_install chromium-browser ;; dnf) pkg_install chromium ;; *) pkg_install chromium ;; esac || true
  fi
fi

# --- Service account ----------------------------------------------------------------------
step "Service account $SERVICE_USER"
if [[ "$OS" == "Linux" ]]; then
  id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$SERVICE_USER"
else
  if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    uid=300; while dscl . -list /Users UniqueID | awk '{print $2}' | grep -qx "$uid"; do uid=$((uid + 1)); done
    dscl . -create "/Users/$SERVICE_USER"
    dscl . -create "/Users/$SERVICE_USER" UserShell /usr/bin/false
    dscl . -create "/Users/$SERVICE_USER" RealName "AiSignX server"
    dscl . -create "/Users/$SERVICE_USER" UniqueID "$uid"
    dscl . -create "/Users/$SERVICE_USER" PrimaryGroupID 20
    dscl . -create "/Users/$SERVICE_USER" NFSHomeDirectory /var/empty
    dscl . -create "/Users/$SERVICE_USER" IsHidden 1
  fi
fi
ok "$(id "$SERVICE_USER")"

# --- Program files (kept separate from the data so an upgrade never touches your media) ----
step "Program files"
service_stop
mkdir -p "$PREFIX" "$DATA/instance" "$DATA/uploads" "$DATA/backups" "$LOG_DIR"
if [[ $UPGRADE -eq 1 && -d "$APP" && -x "$VENV/bin/flask" && -f "$DATA/instance/aisignx.db" ]]; then
  step "Backing up the database before upgrading"
  ( cd "$APP" && env AISIGNX_INSTANCE_DIR="$DATA/instance" AISIGNX_UPLOAD_DIR="$DATA/uploads" AISIGNX_BACKUP_DIR="$DATA/backups" AISIGNX_LOG_DIR="$LOG_DIR" \
      "$VENV/bin/flask" --app wsgi backup create 2>&1 | sed 's/^/    /' ) || warn "Could not make a backup; continuing (copy $DATA/instance by hand if unsure)."
fi
rm -rf "$APP.new"
mkdir -p "$APP.new"
( cd "$SOURCE/server" && tar --anchored --exclude='./.venv' --exclude='./instance' --exclude='./uploads' --exclude='./backups' --exclude='./logs' --exclude='./downloads' --exclude='./plugin-cache' --exclude='./tests' \
    --no-anchored --exclude='__pycache__' --exclude='*.pyc' --exclude='*.db' --exclude='*.db-*' --exclude='.env' --exclude='.pytest_cache' -cf - . ) | tar -xf - -C "$APP.new"
rm -rf "$APP.prev"; [[ -d "$APP" ]] && mv "$APP" "$APP.prev"
mv "$APP.new" "$APP"
ok "Copied the server to $APP"

step "Python environment"
[[ -x "$VENV/bin/python" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$APP/requirements.txt"
ok "Packages installed"

# --- Settings -----------------------------------------------------------------------------
step "Settings file $ENV_FILE"
mkdir -p "$(dirname "$ENV_FILE")"
if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
# AiSignX server settings (see docs/deployment.md for every option). Restart the service after editing:
#   Linux: sudo systemctl restart aisignx    macOS: sudo launchctl kickstart -k system/$LABEL
AISIGNX_INSTANCE_DIR=$DATA/instance
AISIGNX_UPLOAD_DIR=$DATA/uploads
AISIGNX_BACKUP_DIR=$DATA/backups
AISIGNX_LOG_DIR=$LOG_DIR
# AISIGNX_SECURE_COOKIES=1        # turn on when you serve over HTTPS (docs/https.md)
# AISIGNX_MAX_UPLOAD_MB=2048
EOF
  ok "Created"
else
  ok "Kept your existing settings"
fi
chmod 0640 "$ENV_FILE"
chown -R "$SERVICE_USER" "$DATA" 2>/dev/null || true
[[ "$OS" == "Linux" ]] && chgrp "$SERVICE_USER" "$ENV_FILE" 2>/dev/null || true
chmod -R go-rwx "$DATA/instance"   # the database and the secret key

run_as_service() {
  if [[ "$OS" == "Linux" ]]; then runuser -u "$SERVICE_USER" -- env "$@" 2>/dev/null || su -s /bin/sh "$SERVICE_USER" -c "env $*"
  else sudo -u "$SERVICE_USER" env "$@"; fi
}
step "Database"
( set -a; . "$ENV_FILE"; set +a; cd "$APP" && run_as_service AISIGNX_INSTANCE_DIR="$AISIGNX_INSTANCE_DIR" AISIGNX_UPLOAD_DIR="$AISIGNX_UPLOAD_DIR" \
    AISIGNX_BACKUP_DIR="$AISIGNX_BACKUP_DIR" AISIGNX_LOG_DIR="$AISIGNX_LOG_DIR" "$VENV/bin/flask" --app wsgi db upgrade 2>&1 | tail -3 | sed 's/^/    /' )
ok "Database is up to date"

# --- The service --------------------------------------------------------------------------
step "Starting with the system"
if [[ "$OS" == "Linux" ]]; then
  cat > "$UNIT" <<EOF
[Unit]
Description=AiSignX digital signage server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP
EnvironmentFile=$ENV_FILE
ExecStartPre=$VENV/bin/flask --app wsgi db upgrade
ExecStart=$VENV/bin/flask --app wsgi serve --host $HOST --port $PORT
Restart=on-failure
RestartSec=5
# A little hardening: the service can only write to its own data folder.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$DATA

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable aisignx >/dev/null 2>&1
  [[ $START -eq 1 ]] && systemctl restart aisignx
else
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>UserName</key><string>$SERVICE_USER</string>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>ProgramArguments</key><array>
    <string>/bin/sh</string><string>-c</string>
    <string>set -a; . "$ENV_FILE"; set +a; export PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin; "$VENV/bin/flask" --app wsgi db upgrade; exec "$VENV/bin/flask" --app wsgi serve --host $HOST --port $PORT</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$LOG_DIR/service.out.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/service.err.log</string>
</dict></plist>
EOF
  chown root:wheel "$PLIST"; chmod 0644 "$PLIST"
  if [[ $START -eq 1 ]]; then launchctl bootstrap system "$PLIST" 2>/dev/null || launchctl load -w "$PLIST"; fi
fi

if [[ $OPEN_FIREWALL -eq 1 && "$OS" == "Linux" ]]; then
  if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then ufw allow "$PORT/tcp" >/dev/null && ok "Opened port $PORT in ufw"
  elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then firewall-cmd --permanent --add-port="$PORT/tcp" >/dev/null && firewall-cmd --reload >/dev/null && ok "Opened port $PORT in firewalld"; fi
fi

if [[ $START -eq 1 ]]; then
  printf '    waiting for the server'
  for _ in $(seq 1 60); do
    if curl -fsS -o /dev/null "http://127.0.0.1:$PORT/login" 2>/dev/null || curl -sS -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/" 2>/dev/null | grep -qE '^(200|302|503)$'; then UPOK=1; break; fi
    printf '.'; sleep 1
  done; echo
  [[ "${UPOK:-0}" -eq 1 ]] || { warn "The server did not answer within a minute. Look at: $([[ "$OS" == "Linux" ]] && echo "journalctl -u aisignx -n 50" || echo "$LOG_DIR/service.err.log")"; exit 1; }
fi

step "Done"
IPS="$( (hostname -I 2>/dev/null || ipconfig getifaddr en0 2>/dev/null || true) | tr ' ' '\n' | grep -E '^[0-9]+\.' | head -3)"
ok "AiSignX is running and will start with this computer."
for ip in $IPS; do ok "Open http://$ip:$PORT in a browser to finish setup (create the admin account)."; done
[[ -z "$IPS" ]] && ok "Open http://localhost:$PORT in a browser to finish setup (create the admin account)."
echo "    Settings: $ENV_FILE    Data: $DATA"
echo "    $([[ "$OS" == "Linux" ]] && echo "Status: systemctl status aisignx   Logs: journalctl -u aisignx -f" || echo "Logs: $LOG_DIR/service.err.log")"
[[ $UPGRADE -eq 1 ]] && echo "    The previous version is kept in $APP.prev (delete it when you are happy)."
exit 0

if [[ "$HTTPS" == 1 ]]; then
  step "HTTPS with nginx"
  AISIGNX_PREFIX="$PREFIX" AISIGNX_DATA="$DATA" AISIGNX_USER="$SERVICE_USER" "$(dirname "$0")/setup-nginx.sh" --port "$PORT"
else
  echo "    For HTTPS (recommended on shared networks) run scripts/setup-nginx.sh afterwards, or install again with --https."
fi
