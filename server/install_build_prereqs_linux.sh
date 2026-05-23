#!/usr/bin/env bash
# AISignX - Build Prerequisites Installer (Linux / macOS)
# Installs Node.js 20 LTS, Eclipse Temurin JDK 17, Android SDK command-line tools
# Run as a user with sudo access:
#   chmod +x install_build_prereqs_linux.sh && ./install_build_prereqs_linux.sh

set -euo pipefail

CYAN='\033[0;36m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; RED='\033[0;31m'; GRAY='\033[0;90m'; NC='\033[0m'

header() { echo -e "\n${CYAN}==================================================\n  $1\n==================================================${NC}"; }
step()   { echo -e "${YELLOW}[*] $1${NC}"; }
ok()     { echo -e "${GREEN}[OK] $1${NC}"; }
skip()   { echo -e "${GRAY}[SKIP] $1${NC}"; }
fail()   { echo -e "${RED}[FAIL] $1${NC}"; exit 1; }

IS_MAC=false
IS_LINUX=false
PKG=""
[[ "$OSTYPE" == "darwin"* ]]    && IS_MAC=true
[[ "$OSTYPE" == "linux-gnu"* ]] && IS_LINUX=true

ANDROID_HOME="$HOME/android-sdk"
PROFILE_FILE="$HOME/.bashrc"
[[ -f "$HOME/.zshrc" ]] && PROFILE_FILE="$HOME/.zshrc"
[[ "$IS_MAC" == "true" ]] && PROFILE_FILE="$HOME/.zshrc"

header "AISignX Build Prerequisites Installer"
echo -e "  This will install:"
echo -e "  ${GRAY}  - Node.js 20 LTS    (Electron builds)"
echo -e "    - Eclipse Temurin JDK 17  (Android builds)"
echo -e "    - Android SDK command-line tools  (Android builds)${NC}"
echo ""

# =====================================================================
# Detect package manager
# =====================================================================
if [[ "$IS_MAC" == "true" ]]; then
    if ! command -v brew &>/dev/null; then
        fail "Homebrew not found. Install it first: https://brew.sh"
    fi
    PKG="brew"
elif command -v apt-get &>/dev/null; then
    PKG="apt"
elif command -v dnf &>/dev/null; then
    PKG="dnf"
elif command -v pacman &>/dev/null; then
    PKG="pacman"
else
    fail "Unsupported Linux distro - no apt/dnf/pacman found. Install Node.js 20 and JDK 17 manually."
fi
ok "Package manager: $PKG"

# =====================================================================
# 1. Node.js 20 LTS
# =====================================================================
header "Node.js 20 LTS"
if command -v node &>/dev/null && node --version | grep -qE '^v2[0-9]'; then
    skip "Node.js $(node --version) already installed."
else
    if [[ "$PKG" == "apt" ]]; then
        step "Adding NodeSource repository..."
        curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
        sudo apt-get install -y nodejs
    elif [[ "$PKG" == "dnf" ]]; then
        curl -fsSL https://rpm.nodesource.com/setup_20.x | sudo bash -
        sudo dnf install -y nodejs
    elif [[ "$PKG" == "pacman" ]]; then
        sudo pacman -Sy --noconfirm nodejs npm
    elif [[ "$PKG" == "brew" ]]; then
        brew install node@20
        brew link --force --overwrite node@20
    fi
    ok "Node.js $(node --version) installed."
fi

# =====================================================================
# 2. Eclipse Temurin JDK 17
# =====================================================================
header "Eclipse Temurin JDK 17"
JAVA17_OK=false
if command -v java &>/dev/null && java -version 2>&1 | grep -q '"17'; then
    JAVA17_OK=true
fi

if [[ "$JAVA17_OK" == "true" ]]; then
    skip "Java 17 already installed."
else
    if [[ "$PKG" == "apt" ]]; then
        step "Adding Adoptium APT repository..."
        sudo apt-get install -y wget apt-transport-https gnupg
        wget -qO - https://packages.adoptium.net/artifactory/api/gpg/key/public | \
            sudo gpg --dearmor -o /etc/apt/trusted.gpg.d/adoptium.gpg
        echo "deb https://packages.adoptium.net/artifactory/deb $(. /etc/os-release; echo $VERSION_CODENAME) main" | \
            sudo tee /etc/apt/sources.list.d/adoptium.list
        sudo apt-get update -qq
        sudo apt-get install -y temurin-17-jdk
    elif [[ "$PKG" == "dnf" ]]; then
        sudo dnf install -y java-17-openjdk java-17-openjdk-devel
    elif [[ "$PKG" == "pacman" ]]; then
        sudo pacman -Sy --noconfirm jdk17-openjdk
    elif [[ "$PKG" == "brew" ]]; then
        brew install --cask temurin17
    fi
    ok "JDK 17 installed."
fi

# Locate JAVA_HOME
if [[ -z "${JAVA_HOME:-}" ]]; then
    for candidate in \
        /usr/lib/jvm/temurin-17-amd64 \
        /usr/lib/jvm/java-17-openjdk-amd64 \
        /usr/lib/jvm/java-17-openjdk \
        /usr/lib/jvm/java-17 \
        /Library/Java/JavaVirtualMachines/temurin-17.jdk/Contents/Home; do
        if [ -d "$candidate" ]; then
            export JAVA_HOME="$candidate"
            break
        fi
    done
    # Fallback: ask java_home tool (macOS)
    if [[ -z "${JAVA_HOME:-}" ]] && command -v /usr/libexec/java_home &>/dev/null; then
        JAVA_HOME=$(/usr/libexec/java_home -v 17 2>/dev/null || true)
        export JAVA_HOME
    fi
fi
ok "JAVA_HOME: ${JAVA_HOME:-not found - set manually if Android build fails}"

# =====================================================================
# 3. Android SDK command-line tools
# =====================================================================
header "Android SDK Command-Line Tools"

SDKMANAGER="$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager"

if [[ -x "$SDKMANAGER" ]]; then
    skip "Android SDK already installed at $ANDROID_HOME"
else
    step "Downloading Android SDK command-line tools..."
    if [[ "$IS_MAC" == "true" ]]; then
        ZIP_URL="https://dl.google.com/android/repository/commandlinetools-mac-11076708_latest.zip"
    else
        ZIP_URL="https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip"
    fi

    ZIP_TMP="$(mktemp -d)/cmdline-tools.zip"
    curl -fsSL "$ZIP_URL" -o "$ZIP_TMP"
    ok "Downloaded."

    step "Extracting to $ANDROID_HOME/cmdline-tools/latest ..."
    mkdir -p "$ANDROID_HOME/cmdline-tools/latest"
    unzip -q "$ZIP_TMP" -d "$ANDROID_HOME/cmdline-tools/"
    # Google zips the folder as "cmdline-tools/" - move contents to "latest/"
    if [ -d "$ANDROID_HOME/cmdline-tools/cmdline-tools" ]; then
        cp -r "$ANDROID_HOME/cmdline-tools/cmdline-tools/." "$ANDROID_HOME/cmdline-tools/latest/"
        rm -rf "$ANDROID_HOME/cmdline-tools/cmdline-tools"
    fi
    rm -f "$ZIP_TMP"
    ok "Extracted."

    step "Accepting Android licenses and installing SDK packages..."
    export ANDROID_HOME
    yes | "$SDKMANAGER" --licenses >/dev/null 2>&1 || true
    "$SDKMANAGER" "platform-tools" "platforms;android-34" "build-tools;34.0.0"
    ok "Android SDK packages installed."
fi

# =====================================================================
# 4. Add env vars to shell profile
# =====================================================================
header "Updating Shell Profile ($PROFILE_FILE)"

add_to_profile() {
    local line="$1"
    grep -qxF "$line" "$PROFILE_FILE" 2>/dev/null || echo "$line" >> "$PROFILE_FILE"
}

add_to_profile "export ANDROID_HOME=\"$ANDROID_HOME\""
add_to_profile "export PATH=\"\$PATH:\$ANDROID_HOME/cmdline-tools/latest/bin:\$ANDROID_HOME/platform-tools\""
[[ -n "${JAVA_HOME:-}" ]] && add_to_profile "export JAVA_HOME=\"$JAVA_HOME\""

# Apply to current session
export ANDROID_HOME
export PATH="$PATH:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools"

ok "Environment variables written to $PROFILE_FILE"

# =====================================================================
# 5. Optional: wine for Windows cross-compile
# =====================================================================
header "wine (optional - for cross-compiling Windows .exe)"
if command -v wine &>/dev/null; then
    skip "wine already installed: $(wine --version)"
else
    echo -e "${GRAY}  wine is optional. It allows building the Windows AISignX-Player-Setup.exe"
    echo -e "  on Linux. Skip this if you plan to build Windows on a Windows machine.${NC}"
    read -rp "  Install wine? [y/N] " install_wine
    if [[ "${install_wine,,}" == "y" ]]; then
        if [[ "$PKG" == "apt" ]]; then
            sudo apt-get install -y wine
        elif [[ "$PKG" == "dnf" ]]; then
            sudo dnf install -y wine
        elif [[ "$PKG" == "pacman" ]]; then
            sudo pacman -Sy --noconfirm wine
        elif [[ "$PKG" == "brew" ]]; then
            brew install --cask wine-stable
        fi
        ok "wine installed."
    else
        skip "Skipping wine."
    fi
fi

# =====================================================================
# Done
# =====================================================================
header "Prerequisites Installed!"
echo ""
echo -e "  Installed / verified:"
command -v node    &>/dev/null && echo -e "    ${GREEN}Node.js  : $(node --version)${NC}"
command -v npm     &>/dev/null && echo -e "    ${GREEN}npm      : $(npm --version)${NC}"
echo -e "    ${GREEN}JAVA_HOME: ${JAVA_HOME:-not set}${NC}"
echo -e "    ${GREEN}ANDROID_HOME: $ANDROID_HOME${NC}"
echo ""
echo -e "  ${YELLOW}Run the following to apply env vars in your current terminal:${NC}"
echo -e "    ${GREEN}source $PROFILE_FILE${NC}"
echo ""
echo -e "  ${GRAY}Then build all clients with:"
echo -e "    chmod +x build_clients_linux.sh && ./build_clients_linux.sh${NC}"
echo ""
