#!/usr/bin/env bash
# AISignX - Build Clients (Linux / macOS)
# Run from the repo root:
#   ./build_clients_linux.sh
#   ./build_clients_linux.sh --electron --no-bump
#   ./build_clients_linux.sh --android
#   ./build_clients_linux.sh --bump-only
#
# Options:
#   --electron     Build/copy Electron (Linux AppImage + deb; Windows .exe if wine)
#   --android      Build/copy Android APK
#   (default)      Both if neither target flag is set
#   --no-bump      Do not increment versions in source files
#   --bump-only    Bump versions + update client_versions.json only (no compile)
#   --help         Show usage

set -euo pipefail

BUILD_ELECTRON=0
BUILD_ANDROID=0
DO_BUMP=1
DO_BUILD=1
SHOW_HELP=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --electron)  BUILD_ELECTRON=1 ;;
        --android)   BUILD_ANDROID=1 ;;
        --no-bump)   DO_BUMP=0 ;;
        --bump-only) DO_BUILD=0 ;;
        --help|-h)   SHOW_HELP=1 ;;
        *) fail "Unknown option: $1 (try --help)" ;;
    esac
    shift
done

if [[ "$SHOW_HELP" -eq 1 ]]; then
    cat <<'EOF'

AISignX client build script (Linux / macOS)

Usage:
  ./build_clients_linux.sh [options]

Options:
  --electron     Build Electron (Linux + optional Windows via wine)
  --android      Build Android debug APK
  (no target)    Build both

  --no-bump      Skip version bump in package.json / build.gradle.kts
  --bump-only    Only bump versions and update client_versions.json
  --help         Show this help

Examples:
  ./build_clients_linux.sh
  ./build_clients_linux.sh --electron --no-bump
  ./build_clients_linux.sh --android
  ./build_clients_linux.sh --bump-only --electron

EOF
    exit 0
fi

if [[ "$BUILD_ELECTRON" -eq 0 && "$BUILD_ANDROID" -eq 0 ]]; then
    BUILD_ELECTRON=1
    BUILD_ANDROID=1
fi

CYAN='\033[0;36m'
YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
GRAY='\033[0;90m'
NC='\033[0m'

header() { echo -e "\n${CYAN}==================================================\n  $1\n==================================================${NC}"; }
step()   { echo -e "${YELLOW}[*] $1${NC}"; }
ok()     { echo -e "${GREEN}[OK] $1${NC}"; }
skip()   { echo -e "${GRAY}[SKIP] $1${NC}"; }
fail()   { echo -e "${RED}[FAIL] $1${NC}"; exit 1; }

bump_semver_patch() {
    local v="$1"
    if [[ "$v" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}.${BASH_REMATCH[2]}.$((BASH_REMATCH[3] + 1))"
        return 0
    fi
    if [[ "$v" =~ ^([0-9]+)\.([0-9]+)$ ]]; then
        echo "${BASH_REMATCH[1]}.$((BASH_REMATCH[2] + 1))"
        return 0
    fi
    fail "Cannot bump version (expected x.y.z or x.y): $v"
}

sed_inplace() {
    if [[ "$(uname -s)" == Darwin ]]; then
        sed -i '' "$@"
    else
        sed -i "$@"
    fi
}

get_electron_version() {
    grep -E '"version"' "$PKG_JSON" | head -1 | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]+)".*/\1/'
}

get_android_version() {
    grep -E 'versionName' "$GRADLE_FILE" | sed -E 's/.*versionName[[:space:]]*=[[:space:]]*"([^"]+)".*/\1/'
}

bump_electron_version() {
    local old new
    old=$(get_electron_version)
    new=$(bump_semver_patch "$old")
    sed_inplace "s/\"version\"[[:space:]]*:[[:space:]]*\"${old}\"/\"version\": \"${new}\"/" "$PKG_JSON"
    ok "Electron package.json: ${old} -> ${new}"
}

bump_android_version() {
    local old new old_code new_code
    old_code=$(grep -E 'versionCode' "$GRADLE_FILE" | sed -E 's/.*versionCode[[:space:]]*=[[:space:]]*([0-9]+).*/\1/')
    new_code=$((old_code + 1))
    sed_inplace "s/versionCode[[:space:]]*=[[:space:]]*${old_code}/versionCode = ${new_code}/" "$GRADLE_FILE"
    old=$(get_android_version)
    new=$(bump_semver_patch "$old")
    sed_inplace "s/versionName[[:space:]]*=[[:space:]]*\"${old}\"/versionName = \"${new}\"/" "$GRADLE_FILE"
    ok "Android build.gradle.kts: ${old} (code ${old_code}) -> ${new} (code ${new_code})"
}

update_client_versions_json() {
    local ev="$1" av="$2" vf="$3"
    if ! command -v python3 &>/dev/null; then
        fail "python3 required to update client_versions.json"
    fi
    python3 - "$ev" "$av" "$vf" <<'PY'
import json, sys
from pathlib import Path

ev, av, path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
defaults = {
    "windows": ("AISignX-Player-Setup.exe", "/static/clients/AISignX-Player-Setup.exe"),
    "linux_appimage": ("AISignX-Player.AppImage", "/static/clients/AISignX-Player.AppImage"),
    "linux_deb": ("AISignX-Player.deb", "/static/clients/AISignX-Player.deb"),
    "android": ("AISignX-Player.apk", "/static/clients/AISignX-Player.apk"),
}
if path.exists():
    data = json.loads(path.read_text(encoding="utf-8"))
else:
    data = {"version": "", "clients": {}}
clients = data.setdefault("clients", {})
if ev != "-":
    data["version"] = ev
    for key in ("windows", "linux_appimage", "linux_deb"):
        entry = clients.setdefault(key, {})
        entry["version"] = ev
        fn, url = defaults[key]
        entry.setdefault("filename", fn)
        entry.setdefault("url", url)
if av != "-":
    entry = clients.setdefault("android", {})
    entry["version"] = av
    fn, url = defaults["android"]
    entry.setdefault("filename", fn)
    entry.setdefault("url", url)
path.write_text(json.dumps(data, indent=4) + "\n", encoding="utf-8")
PY
}

ROOT="$(cd "$(dirname "$0")" && pwd)"
ELECTRON_DIR="$ROOT/clients/electron-client"
ANDROID_DIR="$ROOT/clients/android-client"
OUTPUT_DIR="$ROOT/server/static/clients"

header "AISignX Client Builder (Linux / macOS)"
echo -e "${GRAY}  Targets: $(
    [[ "$BUILD_ELECTRON" -eq 1 ]] && echo -n 'Electron ')
    [[ "$BUILD_ANDROID" -eq 1 ]] && echo -n 'Android'
)${NC}"
echo -e "${GRAY}  Bump versions: $([[ "$DO_BUMP" -eq 1 ]] && echo yes || echo no)  |  Compile: $([[ "$DO_BUILD" -eq 1 ]] && echo yes || echo 'no (bump-only)')${NC}"

PKG_JSON="$ELECTRON_DIR/package.json"
GRADLE_FILE="$ANDROID_DIR/app/build.gradle.kts"
mkdir -p "$OUTPUT_DIR"

if [[ "$DO_BUMP" -eq 1 ]]; then
    header "Bumping client versions"
    if [[ "$BUILD_ELECTRON" -eq 1 ]]; then bump_electron_version; else skip "Electron version bump skipped."; fi
    if [[ "$BUILD_ANDROID" -eq 1 ]]; then bump_android_version; else skip "Android version bump skipped."; fi
fi

if [[ "$DO_BUILD" -eq 0 ]]; then
    header "Updating client_versions.json"
    EV="-"
    AV="-"
    [[ "$BUILD_ELECTRON" -eq 1 ]] && EV=$(get_electron_version)
    [[ "$BUILD_ANDROID" -eq 1 ]] && AV=$(get_android_version)
    update_client_versions_json "$EV" "$AV" "$OUTPUT_DIR/client_versions.json"
    ok "Manifest updated."
    exit 0
fi

# --- Check Node.js (Electron only) ---
if [[ "$BUILD_ELECTRON" -eq 1 ]]; then
    step "Checking Node.js..."
    if ! command -v node &>/dev/null; then
        fail "Node.js not found. Install from https://nodejs.org and re-run."
    fi
    ok "Node.js $(node --version)"
fi

# =====================================================================
# ELECTRON - Linux + optionally Windows
# =====================================================================
if [[ "$BUILD_ELECTRON" -eq 1 ]]; then
header "Building Electron Client"
cd "$ELECTRON_DIR"

step "Installing npm dependencies..."
npm install --silent
ok "npm install complete."

# Build Linux targets
step "Building Linux AppImage + .deb..."
npm run build:linux
ok "Linux build complete. Artifacts in clients/electron-client/dist/"

# Copy Linux AppImage
step "Copying Linux AppImage to static/clients/..."
APPIMAGE=$(find "$ELECTRON_DIR/dist" -name "*.AppImage" | head -1)
if [ -n "$APPIMAGE" ]; then
    cp "$APPIMAGE" "$OUTPUT_DIR/AISignX-Player.AppImage"
    chmod +x "$OUTPUT_DIR/AISignX-Player.AppImage"
    ok "Copied: AISignX-Player.AppImage"
else
    skip "No .AppImage found in dist."
fi

# Copy Linux .deb
DEB=$(find "$ELECTRON_DIR/dist" -name "*.deb" | head -1)
if [ -n "$DEB" ]; then
    cp "$DEB" "$OUTPUT_DIR/AISignX-Player.deb"
    ok "Copied: AISignX-Player.deb"
else
    skip "No .deb found in dist."
fi

# Cross-compile Windows .exe (requires wine on Linux)
if command -v wine &>/dev/null; then
    step "Building Windows installer (.exe) via wine cross-compile..."
    npm run build:win || true
    EXE=$(find "$ELECTRON_DIR/dist" -name "*.exe" | head -1)
    if [ -n "$EXE" ]; then
        cp "$EXE" "$OUTPUT_DIR/AISignX-Player-Setup.exe"
        ok "Copied: AISignX-Player-Setup.exe"
    else
        skip "Windows .exe not produced - run build_clients_windows.ps1 on a Windows machine."
    fi
else
    skip "wine not found - skipping Windows .exe build. Run build_clients_windows.ps1 on Windows to produce it."
fi

cd "$ROOT"
else
    skip "Electron build skipped."
fi

# =====================================================================
# ANDROID
# =====================================================================
if [[ "$BUILD_ANDROID" -eq 1 ]]; then
header "Building Android Client"

if [ ! -f "$ANDROID_DIR/gradlew" ]; then
    fail "gradlew not found at $ANDROID_DIR/gradlew. Run 'gradle wrapper' inside clients/android-client/ first."
fi
chmod +x "$ANDROID_DIR/gradlew"

if [ -z "${JAVA_HOME:-}" ]; then
    # Try to auto-detect
    if command -v java &>/dev/null; then
        JAVA_HOME=$(java -XshowSettings:all -version 2>&1 | grep "java.home" | awk '{print $3}')
        export JAVA_HOME
        ok "Auto-detected JAVA_HOME: $JAVA_HOME"
    else
        fail "JAVA_HOME is not set and java not found. Install JDK 17 and set JAVA_HOME."
    fi
else
    ok "JAVA_HOME: $JAVA_HOME"
fi

if [ -z "${ANDROID_HOME:-}" ] && [ -z "${ANDROID_SDK_ROOT:-}" ]; then
    # Try common default locations
    for candidate in "$HOME/Android/Sdk" "$HOME/Library/Android/sdk" "/opt/android-sdk"; do
        if [ -d "$candidate" ]; then
            export ANDROID_HOME="$candidate"
            ok "Auto-detected ANDROID_HOME: $ANDROID_HOME"
            break
        fi
    done
    if [ -z "${ANDROID_HOME:-}" ]; then
        fail "ANDROID_HOME is not set. Install Android SDK and set ANDROID_HOME."
    fi
else
    ok "ANDROID_HOME: ${ANDROID_HOME:-$ANDROID_SDK_ROOT}"
fi

cd "$ANDROID_DIR"
step "Running Gradle assembleDebug (debug APK - no signing needed)..."
./gradlew assembleDebug --quiet
ok "Android build complete."

step "Copying APK to static/clients/..."
APK=$(find "$ANDROID_DIR/app/build/outputs/apk/debug" -name "*.apk" | head -1)
if [ -n "$APK" ]; then
    cp "$APK" "$OUTPUT_DIR/AISignX-Player.apk"
    ok "Copied: AISignX-Player.apk"
else
    fail "APK not found in expected location: app/build/outputs/apk/debug/"
fi

cd "$ROOT"
else
    skip "Android build skipped."
fi

# =====================================================================
# Update client_versions.json
# =====================================================================
header "Updating client_versions.json"
EV="-"
AV="-"
[[ "$BUILD_ELECTRON" -eq 1 ]] && EV=$(get_electron_version)
[[ "$BUILD_ANDROID" -eq 1 ]] && AV=$(get_android_version)
update_client_versions_json "$EV" "$AV" "$OUTPUT_DIR/client_versions.json"
[[ "$EV" != "-" ]] && ok "Manifest — windows/linux: ${EV}"
[[ "$AV" != "-" ]] && ok "Manifest — android: ${AV}"

# =====================================================================
# Done
# =====================================================================
header "Build complete"
echo ""
echo -e "  Outputs in server/static/clients/:"
find "$OUTPUT_DIR" \( -name "*.exe" -o -name "*.AppImage" -o -name "*.deb" -o -name "*.apk" \) | while read -r f; do
    size=$(du -sh "$f" | cut -f1)
    echo -e "    ${GREEN}$(basename "$f")${NC}  ($size)"
done
echo ""
echo -e "  ${GRAY}The server will serve these from the Downloads page."
if [[ "$DO_BUMP" -eq 1 ]]; then
    echo -e "  Versions were bumped for selected targets at the start of this script.${NC}"
else
    echo -e "  Version bump was skipped (--no-bump). Manifest reflects current source versions.${NC}"
fi
echo ""
