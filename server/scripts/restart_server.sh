#!/usr/bin/env bash
# Restart AISignX — stop then start_server.sh with the same options.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo "AISignX - restart server"
echo ""

"$SCRIPT_DIR/stop_server.sh"
sleep 1
exec "$SCRIPT_DIR/start_server.sh" "$@"
