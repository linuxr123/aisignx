#!/bin/sh
# AiSignX container start: create the data folders, apply database migrations, then
# run one waitress process (live updates to screens need a single process).
set -eu

for dir in "$AISIGNX_INSTANCE_DIR" "$AISIGNX_UPLOAD_DIR" "$AISIGNX_BACKUP_DIR" "$AISIGNX_LOG_DIR"; do
    if ! mkdir -p "$dir" 2>/dev/null || [ ! -w "$dir" ]; then
        echo "aisignx: cannot write to $dir as uid $(id -u)." >&2
        echo "aisignx: for a bind mount, run on the host: sudo chown -R 1000:1000 <host folder>" >&2
        exit 1
    fi
done

cd /app
flask --app wsgi db upgrade
exec flask --app wsgi serve --host 0.0.0.0 --port 5000
