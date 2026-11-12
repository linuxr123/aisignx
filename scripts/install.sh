#!/usr/bin/env bash
# Install AiSignX on Linux or macOS: Python environment, database, ffmpeg and a web browser.
# Safe to run again; it only installs what is missing.
#
#   ./scripts/install.sh        ask before installing system packages
#   ./scripts/install.sh -y     don't ask
set -euo pipefail

YES=0
[[ "${1:-}" == "-y" || "${1:-}" == "--yes" ]] && YES=1

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVER="$ROOT/server"
VENV="$SERVER/.venv"

step() { printf '\n\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '    \033[32m%s\033[0m\n' "$1"; }
warn() { printf '    \033[33m%s\033[0m\n' "$1"; }

SUDO=""
if [[ $EUID -ne 0 ]] && command -v sudo >/dev/null 2>&1; then SUDO="sudo"; fi

confirm() {
  [[ $YES -eq 1 ]] && return 0
  read -r -p "    $1 is missing. Install it now? [Y/n] " answer
  [[ -z "$answer" || "$answer" =~ ^[Yy] ]]
}

# --- Package manager --------------------------------------------------------
PM=""
for candidate in apt-get dnf pacman zypper brew; do
  if command -v "$candidate" >/dev/null 2>&1; then PM="$candidate"; break; fi
done
OS_ID=""
[[ -r /etc/os-release ]] && OS_ID="$(. /etc/os-release && echo "${ID:-}")"
APT_UPDATED=0

pkg_install() {
  case "$PM" in
    apt-get)
      if [[ $APT_UPDATED -eq 0 ]]; then $SUDO apt-get update -qq; APT_UPDATED=1; fi
      $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$@" ;;
    dnf)    $SUDO dnf install -y -q "$@" ;;
    pacman) $SUDO pacman -S --noconfirm --needed "$@" ;;
    zypper) $SUDO zypper --non-interactive install "$@" ;;
    brew)   brew install "$@" ;;
    *)      return 1 ;;
  esac
}

find_browser() {
  local name
  for name in chromium chromium-browser google-chrome google-chrome-stable microsoft-edge microsoft-edge-stable; do
    if command -v "$name" >/dev/null 2>&1; then command -v "$name"; return 0; fi
  done
  for path in "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
              "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
              "/Applications/Chromium.app/Contents/MacOS/Chromium"; do
    if [[ -x "$path" ]]; then echo "$path"; return 0; fi
  done
  return 1
}

install_browser() {
  case "$PM" in
    apt-get)
      # Ubuntu ships Chromium only as a snap; Google Chrome's .deb works better headless.
      if [[ "$OS_ID" == "ubuntu" && "$(dpkg --print-architecture)" == "amd64" ]]; then
        local deb
        deb="$(mktemp --suffix=.deb)"
        curl -fsSL -o "$deb" https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
        pkg_install "$deb"
        rm -f "$deb"
      else
        pkg_install chromium || pkg_install chromium-browser
      fi ;;
    dnf|pacman|zypper) pkg_install chromium ;;
    brew) brew install --cask google-chrome ;;
    *) return 1 ;;
  esac
}

# --- Python -----------------------------------------------------------------
step "Checking Python 3.10+"
PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$candidate"; break
  fi
done
if [[ -z "$PY" ]]; then
  if confirm "Python 3.10+"; then
    case "$PM" in
      apt-get) pkg_install python3 python3-venv python3-pip ;;
      dnf|zypper) pkg_install python3 python3-pip ;;
      pacman) pkg_install python python-pip ;;
      brew) pkg_install python ;;
    esac
    PY="python3"
  fi
fi
command -v "$PY" >/dev/null 2>&1 || { echo "Python 3.10 or newer is required." >&2; exit 1; }
# Debian/Ubuntu split venv into its own package.
if ! "$PY" -c 'import ensurepip' 2>/dev/null && [[ "$PM" == "apt-get" ]]; then
  pkg_install python3-venv
fi
ok "Using $($PY --version)"

# --- ffmpeg -----------------------------------------------------------------
step "Checking ffmpeg (video length and thumbnails)"
if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ok "ffmpeg found"
elif confirm "ffmpeg" && pkg_install ffmpeg; then
  ok "ffmpeg installed"
else
  warn "Continuing without ffmpeg. Videos will upload without length or thumbnails."
fi

# --- Browser ----------------------------------------------------------------
step "Checking web browser (web page previews)"
if BROWSER="$(find_browser)"; then
  ok "Found $BROWSER"
elif confirm "A Chromium-based browser" && install_browser && BROWSER="$(find_browser)"; then
  ok "Installed $BROWSER"
else
  warn "Continuing without a browser. Web pages will get title cards instead of screenshots."
fi

# --- Python environment -----------------------------------------------------
step "Setting up the Python environment"
[[ -x "$VENV/bin/python" ]] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$SERVER/requirements.txt"
ok "Python packages installed"

# --- Database and final check -----------------------------------------------
step "Creating / updating the database"
cd "$SERVER"
"$VENV/bin/python" -m flask --app wsgi db upgrade

step "Final check"
if "$VENV/bin/python" -m flask --app wsgi check; then
  printf '\n\033[32mAiSignX is installed.\033[0m\n'
else
  printf '\n\033[33mAiSignX is installed, but some optional programs are missing (see above).\033[0m\n'
fi
echo "Start it with:  cd \"$SERVER\" && .venv/bin/flask --app wsgi serve"
echo "Then open http://127.0.0.1:5000 to run the setup wizard."
