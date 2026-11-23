#!/usr/bin/env bash
# One command to install the AiSignX server on Linux: downloads it from GitHub, then runs install-server.sh.
#
#   curl -fsSL https://raw.githubusercontent.com/linuxr123/aisignx/v3/scripts/get-aisignx.sh | sudo bash
#   curl -fsSL .../get-aisignx.sh | sudo bash -s -- --https --port 8080     (options go to install-server.sh)
#   AISIGNX_REF=v3.0.0 ...                                                  (a release tag instead of the branch)
#
# The zip comes over HTTPS. If the release has a .sha256 file next to it, the download is checked against it.
set -euo pipefail

REPO="${AISIGNX_REPO:-linuxr123/aisignx}"
REF="${AISIGNX_REF:-v3}"
[[ "$REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ && "$REF" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "Repo or ref has characters that are not allowed." >&2; exit 1; }
[[ $EUID -eq 0 ]] || { echo "Run this with sudo." >&2; exit 1; }
for t in curl unzip; do command -v "$t" >/dev/null || { echo "Please install '$t' first (for example: apt install curl unzip)." >&2; exit 1; }; done

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
URL="https://github.com/$REPO/archive/$REF.zip"
echo "Downloading $URL"
curl -fsSL "$URL" -o "$WORK/aisignx.zip"

if SUM="$(curl -fsSL "$URL.sha256" 2>/dev/null)" && [[ -n "$SUM" ]]; then
  [[ "$(sha256sum "$WORK/aisignx.zip" | cut -d' ' -f1)" == "${SUM%% *}" ]] || { echo "The download does not match its published checksum. Nothing was installed." >&2; exit 1; }
  echo "Checksum matches."
else
  echo "No published checksum for this download (normal for a branch)."
fi

unzip -q "$WORK/aisignx.zip" -d "$WORK"
ROOT="$(find "$WORK" -mindepth 1 -maxdepth 1 -type d | head -n1)"
[[ -f "$ROOT/scripts/install-server.sh" ]] || { echo "The download does not contain scripts/install-server.sh." >&2; exit 1; }
bash "$ROOT/scripts/install-server.sh" --source "$ROOT" "$@"
