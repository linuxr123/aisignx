# AISignX — Production Deployment

How to deploy AISignX in production with nginx, systemd, SSL, and optional Cloudflare.

---

## Overview

A production deployment consists of:

```
Internet / LAN
      |
 [nginx / Caddy]   <-- SSL termination, reverse proxy
      |
 [Gunicorn / Waitress]  <-- WSGI server running AISignX
      |
 [AISignX (Flask)]
      |
 [SQLite / PostgreSQL]
```

---

## 1. Linux Production Setup (Ubuntu / Debian)

### 1.1 Install System Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv ffmpeg nginx git
```

### 1.2 Create a System User

```bash
sudo useradd -m -s /bin/bash aisignx
sudo mkdir -p /opt/AISignXV2
sudo chown aisignx:aisignx /opt/AISignXV2
```

### 1.3 Deploy the Application

```bash
sudo -u aisignx git clone https://github.com/yourorg/AISignXV2.git /opt/AISignXV2
cd /opt/AISignXV2
sudo -u aisignx python3 -m venv venv
sudo -u aisignx venv/bin/pip install -r requirements.txt
sudo -u aisignx venv/bin/pip install gunicorn
sudo -u aisignx venv/bin/playwright install chromium
sudo -u aisignx python generate_config.py
sudo -u aisignx python migration.py
```

### 1.4 Create the systemd Service

Create `/etc/systemd/system/aisignx.service`:

```ini
[Unit]
Description=AISignX Digital Signage Server
After=network.target

[Service]
User=aisignx
Group=aisignx
WorkingDirectory=/opt/AISignXV2
ExecStart=/opt/AISignXV2/venv/bin/gunicorn \
    --workers 4 \
    --bind 127.0.0.1:5000 \
    --timeout 120 \
    --keep-alive 75 \
    --worker-class sync \
    app:app
Restart=always
RestartSec=5
Environment=FLASK_ENV=production

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable aisignx
sudo systemctl start aisignx
sudo systemctl status aisignx
```

### 1.5 Configure nginx

Create `/etc/nginx/sites-available/aisignx`:

```nginx
server {
    listen 80;
    server_name signage.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name signage.example.com;

    ssl_certificate     /etc/ssl/certs/signage.crt;
    ssl_certificate_key /etc/ssl/private/signage.key;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;

    # Increase max upload size for media files
    client_max_body_size 500M;

    # Static files served directly by nginx (faster than Flask)
    location /static/ {
        alias /opt/AISignXV2/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location /uploads/ {
        alias /opt/AISignXV2/uploads/;
        expires 1d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # Required for SSE (Server-Sent Events — live push to displays)
        # Do NOT remove these — removing them will break live display updates
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/aisignx /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### 1.6 SSL Certificate (Let's Encrypt)

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d signage.example.com
```

Certbot auto-renews. Test renewal: `sudo certbot renew --dry-run`

---

## 2. Windows Production Setup

### 2.1 Install Waitress

```bash
pip install waitress
```

### 2.2 Create a Startup Script

Create `start_server.bat`:
```bat
@echo off
cd /d C:\AISignXV2
call venv\Scripts\activate
waitress-serve --port=5000 --threads=4 app:app
```

### 2.3 Run as a Windows Service

Use NSSM (Non-Sucking Service Manager):

1. Download NSSM from https://nssm.cc
2. Install the service:
```cmd
nssm install AISignX "C:\AISignXV2\venv\Scripts\waitress-serve.exe"
nssm set AISignX AppParameters --port=5000 --threads=4 app:app
nssm set AISignX AppDirectory C:\AISignXV2
nssm set AISignX Start SERVICE_AUTO_START
nssm start AISignX
```

### 2.4 nginx on Windows

Download nginx for Windows from https://nginx.org/en/download.html and use the same nginx config as the Linux example above.

---

## 3. Using Caddy Instead of nginx

Caddy auto-provisions SSL certificates. Create `Caddyfile`:

```
signage.example.com {
    reverse_proxy 127.0.0.1:5000 {
        flush_interval -1
        transport http {
            read_buffer_size 0
        }
    }

    # Serve static files directly
    handle_path /static/* {
        root * /opt/AISignXV2
        file_server
    }

    handle_path /uploads/* {
        root * /opt/AISignXV2
        file_server
    }
}
```

> **Important:** The `flush_interval -1` directive is required for SSE to work correctly with Caddy.

---

## 4. PostgreSQL (Recommended for Production)

SQLite works well for small deployments (under ~20 displays). For larger deployments use PostgreSQL.

### Setup

```bash
sudo apt install postgresql postgresql-contrib
sudo -u postgres createuser aisignx --pwprompt
sudo -u postgres createdb aisignx --owner=aisignx
pip install psycopg2-binary
```

### config.py

```python
SQLALCHEMY_DATABASE_URI = 'postgresql://aisignx:your-password@localhost/aisignx'
```

### Migrate

```bash
python migration.py
```

---

## 5. Behind Cloudflare

If using Cloudflare as your CDN/proxy:

In `config.py`:
```python
TRUST_PROXY = True
TRUST_PROXY_HOPS = 2   # Cloudflare -> nginx -> Flask = 2 hops
PREFERRED_URL_SCHEME = 'https'
```

**Important Cloudflare settings:**
- Set the SSL/TLS mode to **Full (Strict)** — not Flexible
- Disable Cloudflare caching for the `/display/` and `/api/` paths, or SSE will break
- Add a Page Rule: `signage.example.com/display/*` ? Cache Level: Bypass
- Add a Page Rule: `signage.example.com/api/*` ? Cache Level: Bypass

---

## 6. File Storage

### Upload Directory Permissions (Linux)

```bash
sudo chown -R aisignx:aisignx /opt/AISignXV2/uploads
sudo chmod -R 755 /opt/AISignXV2/uploads
```

### NAS / External Storage

To store uploads on a NAS mount:

1. Mount the NAS:
```bash
sudo mount -t nfs 192.168.1.100:/media /mnt/aisignx-media
```

2. Update `config.py`:
```python
UPLOAD_FOLDER = '/mnt/aisignx-media'
```

3. Move existing uploads:
```bash
mv /opt/AISignXV2/uploads/* /mnt/aisignx-media/
```

Add the mount to `/etc/fstab` for persistence across reboots.

---

## 7. Backups

### Automated Backup Script (Linux)

Create `/opt/backup-aisignx.sh`:
```bash
#!/bin/bash
BACKUP_DIR="/var/backups/aisignx"
DATE=$(date +%Y%m%d-%H%M)
mkdir -p "$BACKUP_DIR"

# Database
cp /opt/AISignXV2/signage.db "$BACKUP_DIR/signage-$DATE.db"

# Uploads
tar -czf "$BACKUP_DIR/uploads-$DATE.tar.gz" -C /opt/AISignXV2 uploads/

# Config
cp /opt/AISignXV2/config.py "$BACKUP_DIR/config-$DATE.py"

# Keep only last 14 days
find "$BACKUP_DIR" -mtime +14 -delete

echo "Backup complete: $BACKUP_DIR"
```

```bash
chmod +x /opt/backup-aisignx.sh
# Add to cron for daily 2am backups:
echo "0 2 * * * /opt/backup-aisignx.sh" | sudo crontab -u aisignx -
```

---

## 8. Firewall

Open only the ports you need:

```bash
sudo ufw allow 22    # SSH
sudo ufw allow 80    # HTTP (redirect to HTTPS)
sudo ufw allow 443   # HTTPS
sudo ufw enable
```

If not using a reverse proxy, open port 5000 directly:
```bash
sudo ufw allow 5000
```

---

## 9. Performance Tuning

### Gunicorn Workers

A good rule of thumb: `workers = (2 * CPU_cores) + 1`

```bash
gunicorn -w 5 -b 127.0.0.1:5000 app:app
```

### SSE and Long-Lived Connections

Each connected display holds an open SSE connection. With many displays, use `--timeout 0` or a high value to prevent Gunicorn from killing long-running connections:

```bash
gunicorn -w 4 -b 127.0.0.1:5000 --timeout 120 app:app
```

### nginx Worker Connections

For many simultaneous SSE connections, increase nginx worker connections:

```nginx
events {
    worker_connections 4096;
}
```

---

## 10. Health Check

A simple health check endpoint is available:

```http
GET /api/version
```

Returns the server version and client manifest. Use this for uptime monitoring (UptimeRobot, Zabbix, etc.).

Example Zabbix check:
```
HTTP Agent: https://signage.example.com/api/version
Expected status: 200
```