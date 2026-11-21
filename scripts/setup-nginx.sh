#!/usr/bin/env bash
# Puts nginx in front of the AiSignX server on Linux, with HTTPS (self-signed, one you bought, or Let's Encrypt).
#
# Run it AFTER scripts/install-server.sh, as root:
#   sudo scripts/setup-nginx.sh --host signs.example.lan --host 192.168.1.19
#   sudo scripts/setup-nginx.sh --cert fullchain.pem --key privkey.pem
#   sudo scripts/setup-nginx.sh --letsencrypt signs.example.com --email you@example.com   (free; needs a public name and port 80)
#   sudo scripts/setup-nginx.sh --update        # nginx comes from your distribution: this upgrades that package
#   sudo scripts/setup-nginx.sh --uninstall
#
# What it does: installs nginx with the system package manager (so security updates come with the system), gets
# the certificate (AiSignX makes a self-signed one, or checks the one you give), writes /etc/nginx/conf.d/aisignx.conf
# (made by `flask https config`), checks it with `nginx -t`, turns on AISIGNX_PROXY_HOPS=1 and AISIGNX_SECURE_COOKIES=1,
# restarts AiSignX, and installs a systemd path unit that reloads nginx whenever the certificate file changes (for
# example after you replace it on the admin page System > HTTPS, or when certbot renews it).
#
# Honest limits: tested for syntax and for the text it generates; not yet run on a real Linux machine
# (docs/testing-checklist.md). Linux is supported on a best-effort basis.
set -euo pipefail

HOSTS=(); CERT=""; KEY=""; PASSPHRASE=""; LE_DOMAIN=""; LE_EMAIL=""
PORT=5000; HTTP_PORT=80; HTTPS_PORT=443; HSTS=0; REDIRECT=1; FIREWALL=1
ACTION=install; DRY=0; PREFIX="${AISIGNX_PREFIX:-/opt/aisignx}"; DATA="${AISIGNX_DATA:-/var/lib/aisignx}"
SERVICE_USER="${AISIGNX_USER:-aisignx}"; ENV_FILE="/etc/aisignx/aisignx.env"

usage() { sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
step() { printf '\n==> %s\n' "$*"; }
ok()   { printf '    %s\n' "$*"; }
warn() { printf '    ! %s\n' "$*" >&2; }
die()  { printf '\nProblem: %s\n' "$*" >&2; exit 1; }
run()  { if [[ $DRY == 1 ]]; then printf '    [dry run] %s\n' "$*"; else "$@"; fi; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) HOSTS+=("${2:?--host needs a name or address}"); shift 2 ;;
    --cert) CERT="${2:?--cert needs a file}"; shift 2 ;;
    --key) KEY="${2:?--key needs a file}"; shift 2 ;;
    --passphrase) PASSPHRASE="${2:?}"; shift 2 ;;
    --letsencrypt) LE_DOMAIN="${2:?--letsencrypt needs a domain name}"; shift 2 ;;
    --email) LE_EMAIL="${2:?--email needs an address}"; shift 2 ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --http-port) HTTP_PORT="${2:?}"; shift 2 ;;
    --https-port) HTTPS_PORT="${2:?}"; shift 2 ;;
    --hsts) HSTS=1; shift ;;
    --no-redirect) REDIRECT=0; shift ;;
    --no-firewall) FIREWALL=0; shift ;;
    --update) ACTION=update; shift ;;
    --uninstall) ACTION=uninstall; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

# Everything below ends up in a config file or a command line: only plain names, addresses and numbers go in.
for h in "${HOSTS[@]:-}"; do [[ -z "$h" || "$h" =~ ^[A-Za-z0-9.:*-]{1,253}$ ]] || die "'$h' is not a host name or an IP address."; done
[[ -z "$LE_DOMAIN" || "$LE_DOMAIN" =~ ^[A-Za-z0-9.-]{1,253}$ ]] || die "'$LE_DOMAIN' is not a domain name."
[[ -z "$LE_EMAIL" || "$LE_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$ ]] || die "'$LE_EMAIL' is not an e-mail address."
for p in "$PORT" "$HTTP_PORT" "$HTTPS_PORT"; do [[ "$p" =~ ^[0-9]{1,5}$ && "$p" -ge 1 && "$p" -le 65535 ]] || die "Ports must be numbers from 1 to 65535."; done
[[ -n "$CERT" || -z "$KEY" ]] || die "Give both --cert and --key."
[[ -z "$CERT" || -n "$KEY" ]] || die "Give both --cert and --key."
[[ -n "$LE_DOMAIN" && -z "$LE_EMAIL" ]] && die "--letsencrypt needs --email (Let's Encrypt writes to it about expiring certificates)."
[[ "$(uname -s)" == "Linux" ]] || die "This script is for Linux. (macOS is not supported by AiSignX.)"
if [[ $EUID -ne 0 && $DRY == 0 ]]; then die "Run it as root: sudo $0 ..."; fi

APP="$PREFIX/app"; VENV="$PREFIX/venv"; TLS="$DATA/tls"; CONF="/etc/nginx/conf.d/aisignx.conf"
ACME_ROOT="/var/www/aisignx-acme"
[[ -x "$VENV/bin/flask" && -f "$ENV_FILE" ]] || { [[ $DRY == 1 ]] || die "The AiSignX server is not installed ($PREFIX). Run scripts/install-server.sh first."; }

flask_cmd() {  # as the service user, with the server's settings
  ( set -a; . "$ENV_FILE"; set +a; export AISIGNX_TLS_DIR="$TLS"; cd "$APP" &&
    if command -v runuser >/dev/null 2>&1; then runuser -u "$SERVICE_USER" -- env AISIGNX_TLS_DIR="$TLS" AISIGNX_INSTANCE_DIR="$AISIGNX_INSTANCE_DIR" \
        AISIGNX_UPLOAD_DIR="$AISIGNX_UPLOAD_DIR" AISIGNX_BACKUP_DIR="$AISIGNX_BACKUP_DIR" AISIGNX_LOG_DIR="$AISIGNX_LOG_DIR" "$VENV/bin/flask" --app wsgi "$@"
    else su -s /bin/sh "$SERVICE_USER" -c "AISIGNX_TLS_DIR='$TLS' '$VENV/bin/flask' --app wsgi $*"; fi )
}
set_env() {  # set_env NAME VALUE: replace (or add) a line in the server's env file
  local name="$1" value="$2"
  if grep -Eq "^[[:space:]]*#?[[:space:]]*${name}=" "$ENV_FILE"; then sed -i -E "0,/^[[:space:]]*#?[[:space:]]*${name}=.*/s//${name}=${value}/" "$ENV_FILE"
  else printf '%s=%s\n' "$name" "$value" >> "$ENV_FILE"; fi
}
pkg() {
  if command -v apt-get >/dev/null 2>&1; then DEBIAN_FRONTEND=noninteractive apt-get "$@"
  elif command -v dnf >/dev/null 2>&1; then dnf -y "$@"
  elif command -v yum >/dev/null 2>&1; then yum -y "$@"
  elif command -v zypper >/dev/null 2>&1; then zypper --non-interactive "$@"
  else die "No supported package manager (apt, dnf, yum, zypper). Install nginx yourself and use the page System > HTTPS to download the configuration."; fi
}
install_pkg() { if command -v apt-get >/dev/null 2>&1; then pkg install -y "$@"; else pkg install "$@"; fi; }
upgrade_pkg() { if command -v apt-get >/dev/null 2>&1; then pkg install -y --only-upgrade "$@"; elif command -v zypper >/dev/null 2>&1; then pkg update "$@"; else pkg upgrade "$@"; fi; }

write_config() {
  local args=(https config --out "$CONF" --upstream "127.0.0.1:$PORT" --cert-path "$TLS/server.crt" --key-path "$TLS/server.key")
  [[ $HSTS == 1 ]] && args+=(--hsts); [[ $REDIRECT == 0 ]] && args+=(--no-redirect)
  [[ -n "$LE_DOMAIN" ]] && args+=(--acme-root "$ACME_ROOT")
  for h in "${HOSTS[@]:-}"; do [[ -n "$h" ]] && args+=(--name "$h"); done
  # The service user writes the file into /etc/nginx through a temp location, then root moves it into place.
  local tmp; tmp="$(mktemp)"; chmod 644 "$tmp"; chown "$SERVICE_USER" "$tmp" 2>/dev/null || true
  args[3]="$tmp"
  flask_cmd "${args[@]}"
  [[ -f "$CONF" ]] && cp -a "$CONF" "$CONF.bak"
  install -m 644 "$tmp" "$CONF"; rm -f "$tmp"
}
check_and_reload() {
  if nginx -t 2>&1 | sed 's/^/    /'; [[ ${PIPESTATUS[0]} -ne 0 ]]; then
    [[ -f "$CONF.bak" ]] && { cp -a "$CONF.bak" "$CONF"; warn "nginx rejected the new configuration: the previous one was put back."; } || rm -f "$CONF"
    die "nginx does not accept the configuration (see above)."
  fi
  systemctl enable nginx >/dev/null 2>&1 || true
  systemctl reload nginx 2>/dev/null || systemctl restart nginx
}

if [[ $ACTION == uninstall ]]; then
  step "Removing the AiSignX nginx configuration (nginx itself, the certificate and the server stay)"
  run systemctl disable --now aisignx-nginx-reload.path 2>/dev/null || true
  run rm -f /etc/systemd/system/aisignx-nginx-reload.path /etc/systemd/system/aisignx-nginx-reload.service "$CONF" "$CONF.bak" /etc/letsencrypt/renewal-hooks/deploy/aisignx.sh
  run systemctl daemon-reload
  run systemctl reload nginx 2>/dev/null || true
  warn "AISIGNX_PROXY_HOPS and AISIGNX_SECURE_COOKIES are still on in $ENV_FILE: turn them off if you go back to plain http."
  exit 0
fi

if [[ $ACTION == update ]]; then
  step "Upgrading nginx (from your distribution's package repository)"
  command -v nginx >/dev/null 2>&1 || die "nginx is not installed. Run this script without --update first."
  before="$(nginx -v 2>&1)"; ok "now: $before"
  run upgrade_pkg nginx
  step "Checking the configuration with the new nginx"
  run nginx -t
  run systemctl restart nginx
  ok "now: $(nginx -v 2>&1)"
  exit 0
fi

step "nginx in front of AiSignX (HTTPS on port $HTTPS_PORT)"
step "nginx"
if command -v nginx >/dev/null 2>&1; then ok "$(nginx -v 2>&1) is already installed"; else run install_pkg nginx; fi

step "Certificate"
run install -d -m 750 -o "$SERVICE_USER" -g "$SERVICE_USER" "$TLS"
if [[ -n "$CERT" ]]; then
  [[ -f "$CERT" && -f "$KEY" ]] || { [[ $DRY == 1 ]] || die "The certificate or key file was not found."; }
  # Hand the files to the service user through a private temp folder (it cannot read root's files).
  if [[ $DRY == 0 ]]; then
    T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT; cp "$CERT" "$T/c.pem"; cp "$KEY" "$T/k.pem"; chown -R "$SERVICE_USER" "$T"; chmod 700 "$T"
    imp=(https import --cert "$T/c.pem" --key "$T/k.pem"); [[ -n "$PASSPHRASE" ]] && imp+=(--passphrase "$PASSPHRASE")
    flask_cmd "${imp[@]}"
  else ok "[dry run] check and store $CERT"; fi
elif [[ ! -f "$TLS/server.crt" ]]; then
  if [[ ${#HOSTS[@]} -eq 0 ]]; then HOSTS=("$(hostname -f 2>/dev/null || hostname)"); while read -r ip; do HOSTS+=("$ip"); done < <(hostname -I 2>/dev/null | tr ' ' '\n' | grep -E '^[0-9]+\.' | head -3); fi
  [[ -n "$LE_DOMAIN" ]] && HOSTS=("$LE_DOMAIN")
  ss=(https self-signed); for h in "${HOSTS[@]}"; do ss+=(--name "$h"); done
  run flask_cmd "${ss[@]}"
  [[ -z "$LE_DOMAIN" ]] && warn "A self-signed certificate: browsers warn until it is trusted. Install $TLS/server.crt as a trusted certificate on your PCs, or let the Android and Windows players pin it (docs/https.md)."
else ok "Using the certificate that is already stored (System > HTTPS shows it)."; fi

step "nginx configuration"
if [[ $DRY == 1 ]]; then ok "[dry run] write $CONF and check it with nginx -t"; else
  [[ -n "$LE_DOMAIN" ]] && install -d -m 755 "$ACME_ROOT"
  write_config; check_and_reload; ok "nginx -t: the configuration is valid"
fi

step "AiSignX settings"
if [[ $DRY == 0 ]]; then
  set_env AISIGNX_PROXY_HOPS 1; set_env AISIGNX_SECURE_COOKIES 1; set_env AISIGNX_TLS_DIR "$TLS"
  systemctl restart aisignx 2>/dev/null || warn "Could not restart the aisignx service: restart it yourself."
else ok "[dry run] AISIGNX_PROXY_HOPS=1, AISIGNX_SECURE_COOKIES=1, restart aisignx"; fi

step "Reload nginx when the certificate changes"
if [[ $DRY == 0 ]]; then
  cat > /etc/systemd/system/aisignx-nginx-reload.path <<EOF
[Unit]
Description=Watch the AiSignX HTTPS certificate

[Path]
PathChanged=$TLS/server.crt

[Install]
WantedBy=multi-user.target
EOF
  cat > /etc/systemd/system/aisignx-nginx-reload.service <<EOF
[Unit]
Description=Reload nginx after the AiSignX certificate changed

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'nginx -t && systemctl reload nginx'
EOF
  systemctl daemon-reload; systemctl enable --now aisignx-nginx-reload.path >/dev/null
else ok "[dry run] systemd path unit aisignx-nginx-reload"; fi

if [[ -n "$LE_DOMAIN" ]]; then
  step "Let's Encrypt for $LE_DOMAIN"
  if [[ $DRY == 1 ]]; then ok "[dry run] certbot certonly --webroot, then import the certificate"; else
    command -v certbot >/dev/null 2>&1 || install_pkg certbot
    certbot certonly --webroot -w "$ACME_ROOT" -d "$LE_DOMAIN" -m "$LE_EMAIL" --agree-tos --non-interactive --keep-until-expiring
    L="/etc/letsencrypt/live/$LE_DOMAIN"
    # Every renewal: certbot runs this hook, which stores the new certificate for AiSignX (the path unit reloads nginx).
    install -d /etc/letsencrypt/renewal-hooks/deploy
    cat > /etc/letsencrypt/renewal-hooks/deploy/aisignx.sh <<EOF
#!/bin/sh
T=\$(mktemp -d); cp "$L/fullchain.pem" "\$T/c.pem"; cp "$L/privkey.pem" "\$T/k.pem"; chown -R $SERVICE_USER "\$T"; chmod 700 "\$T"
cd "$APP" && runuser -u $SERVICE_USER -- env AISIGNX_TLS_DIR="$TLS" $(grep -E '^AISIGNX_(INSTANCE|UPLOAD|BACKUP|LOG)_DIR=' "$ENV_FILE" | tr '\n' ' ') "$VENV/bin/flask" --app wsgi https import --cert "\$T/c.pem" --key "\$T/k.pem"
rm -rf "\$T"
EOF
    chmod 755 /etc/letsencrypt/renewal-hooks/deploy/aisignx.sh
    sh /etc/letsencrypt/renewal-hooks/deploy/aisignx.sh
    ok "Let's Encrypt certificate stored; certbot renews it by itself and AiSignX and nginx pick up each renewal."
  fi
fi

if [[ $FIREWALL == 1 ]]; then
  step "Firewall"
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    run ufw allow "$HTTPS_PORT/tcp"; [[ $REDIRECT == 1 ]] && run ufw allow "$HTTP_PORT/tcp"
    ok "Port $PORT (plain http) is not opened by this script; if you opened it earlier, close it: ufw delete allow $PORT/tcp"
  elif command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
    run firewall-cmd --permanent --add-port="$HTTPS_PORT/tcp"; [[ $REDIRECT == 1 ]] && run firewall-cmd --permanent --add-port="$HTTP_PORT/tcp"; run firewall-cmd --reload
  else ok "No active ufw or firewalld found: nothing changed."; fi
fi

step "Done"
ok "Open https://${HOSTS[0]:-this-computer}$([[ $HTTPS_PORT != 443 ]] && echo ":$HTTPS_PORT")/ in a browser."
ok "For the best protection AiSignX should only listen on this machine: install it with --host 127.0.0.1 (scripts/install-server.sh)."
ok "Screens that were set up with an http:// address must be set up again with the https:// one."
