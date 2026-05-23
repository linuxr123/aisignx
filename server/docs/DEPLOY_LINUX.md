# AISignXV2 — Linux single-server deployment

This is the supported production path: one Linux host, one Docker
container, your own reverse proxy in front for HTTPS.

## 0. Host requirements

- Linux x86_64 (Ubuntu 22.04 LTS / Debian 12 / RHEL 9 all tested in spec)
- 2 vCPU, 4 GB RAM, 50 GB disk minimum (scales to ~1,000 displays)
- Docker Engine 24+ and the Compose plugin (`docker compose ...`)
- Outbound TCP 443 only if you plan to download plugins/updates;
  otherwise the server is fully offline-capable

```sh
# Ubuntu/Debian quick install
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER     # log out + back in
docker --version && docker compose version
```

## 1. Get the code

```sh
git clone <your-repo-url> aisignx
cd aisignx
```

## 2. Configure

Create `.env` in the repo root. The compose file refuses to start without
`AISIGNX_SECRET_KEY`.

```sh
cat > .env <<EOF
AISIGNX_SECRET_KEY=$(openssl rand -hex 32)
EOF
chmod 600 .env
```

Optional overrides (defaults shown):

| Variable             | Default                           | Notes                                    |
|----------------------|-----------------------------------|------------------------------------------|
| `AISIGNX_DB_PATH`    | `/data/db/signage.sqlite`         | Inside the container                     |
| `UPLOAD_FOLDER`      | `/data/uploads`                   | Tenant media + thumbnails                |
| `AISIGNX_LOG_DIR`    | `/data/logs`                      | App + access logs                        |
| `TRUST_PROXY_HOPS`   | `1`                               | `2` if behind Cloudflare → nginx         |

## 3. Build + start

```sh
docker compose up -d --build
docker compose logs -f aisignx        # Ctrl-C to detach; container keeps running
```

First boot creates user **`admin` / `Admin123!`** — change immediately
under *User Management*.

Health check: `curl -fsS http://localhost:5000/static/sw.js` returns 200.

## 4. Reverse proxy (nginx + Let's Encrypt)

Minimal nginx site config:

```nginx
server {
    listen 443 ssl http2;
    server_name signage.example.com;

    ssl_certificate     /etc/letsencrypt/live/signage.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/signage.example.com/privkey.pem;

    client_max_body_size 2048m;          # large media uploads

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # SSE: long-lived, unbuffered, no read timeout
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 24h;
    }
}
```

If you put Cloudflare in front, set `TRUST_PROXY_HOPS=2` in `.env` and
restart the container so `request.remote_addr` resolves to the real
client IP.

## 5. Persistence + backup

Three named volumes hold all state:

- `aisignx_db`       — SQLite database
- `aisignx_uploads`  — media files
- `aisignx_logs`     — runtime logs

Snapshot daily (example):

```sh
ts=$(date +%Y%m%d-%H%M%S)
mkdir -p /backups/aisignx
docker run --rm \
    -v aisignx_db:/db -v aisignx_uploads:/uploads \
    -v /backups/aisignx:/out \
    alpine tar czf /out/aisignx-$ts.tgz /db /uploads
```

The app also exposes admin-driven backups at `/admin/backups`.

## 6. Verify after install

```sh
curl -fsS http://localhost:5000/static/sw.js >/dev/null && echo "static OK"
docker compose exec aisignx python - <<'PY'
from app import app
print('routes:', sum(1 for _ in app.url_map.iter_rules()))
PY
```

Then in the browser:

- `https://<host>/`                  log in
- `https://<host>/displays`          register a test display
- `https://<host>/admin/proof-of-play` enable PoP under Settings, then check rows appear
- `https://<host>/admin/plugin-policy` per-tenant plugin gating

## 7. Updating

```sh
cd aisignx
git pull
docker compose up -d --build         # data volumes survive rebuild
```

`db.create_all()` runs at boot, so additive schema changes (new columns
on existing models, new tables) auto-apply. Destructive changes go
through `migrations/versions/` — read those before upgrading across a
breaking release.
