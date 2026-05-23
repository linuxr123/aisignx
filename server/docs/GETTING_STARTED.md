# AISignX ù Getting Started

Complete installation guide for a fresh server setup.

> All commands in sections 3ñ8 below are run from the **`server/`** directory
> unless noted otherwise.

> **Quick install:** Use the install script to handle steps 3ù7 automatically.
>
> **Windows (run as Administrator, from the `server/` directory):**
> ```powershell
> cd server
> powershell -ExecutionPolicy Bypass -File install_windows.ps1
> ```
> **Linux / macOS:**
> ```bash
> cd server
> chmod +x install_linux.sh && ./install_linux.sh
> ```
> Continue reading for a manual step-by-step walkthrough or to understand what the script does.

---

## Requirements

| Requirement | Notes |
|---|---|
| Python 3.10+ | 3.11 or 3.12 recommended |
| pip | Included with Python |
| FFmpeg | Required for video duration detection |
| Chromium (Playwright) | Installed automatically ù for webpage thumbnails |
| 1 GB RAM minimum | 2 GB recommended for production |
| Windows, Linux, or macOS | All supported |

---

## 1. Install FFmpeg

FFmpeg is required for video uploads (duration detection and thumbnail generation).

**Windows:**
1. Download from https://ffmpeg.org/download.html
2. Extract and add the `bin/` folder to your system PATH
3. Verify: `ffmpeg -version`

**Linux (Debian/Ubuntu):**
```bash
sudo apt update && sudo apt install ffmpeg
```

**macOS:**
```bash
brew install ffmpeg
```

---

## 2. Get the Code

```bash
git clone https://github.com/yourorg/AISignXV2.git
cd AISignXV2
```

Or download and extract the ZIP archive from your repository.

---

## 3. Create a Virtual Environment

```bash
python -m venv venv

# Windows:
venv\Scripts\activate

# Linux / macOS:
source venv/bin/activate
```

---

## 4. Install Python Dependencies

```bash
pip install -r requirements.txt
```

---

## 5. Install Playwright Browser

Required for webpage thumbnail generation. Only the Chromium binary is needed.

```bash
playwright install chromium
```

---

## 6. Generate Config

```bash
python generate_config.py
```

Creates `config.py` with a randomly generated `SECRET_KEY`. Safe to re-run ù skips if `config.py` already exists.

### Key settings in config.py

| Setting | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | Flask session secret ù keep this private and never commit it |
| `SQLALCHEMY_DATABASE_URI` | `sqlite:///digital_signage.db` | Database ù switch to PostgreSQL for high-traffic production |
| `UPLOAD_FOLDER` | `uploads` | Directory where uploaded media files are stored |
| `TRUST_PROXY` | `True` | Set `False` if running directly without a reverse proxy |
| `TRUST_PROXY_HOPS` | `1` | Set `2` if behind Cloudflare/CDN + nginx |
| `PREFERRED_URL_SCHEME` | `https` | Change to `http` for plain HTTP setups |

---

## 7. Initialize the Database

```bash
python migration.py
```

This single command handles everything on both fresh installs and upgrades:
- Creates the `migrations/` folder if it does not exist
- Generates a migration script from the current models
- Applies all pending migrations

> **Important:** Never run `flask db upgrade` directly on a fresh install ù it will fail because `migrations/` does not exist yet. Always use `python migration.py`.

---

## 8. Start the Server

**Development (auto-reload on code changes):**
```bash
python app.py
```

**Production ù Linux (Gunicorn):**
```bash
pip install gunicorn
gunicorn -w 4 -b 0.0.0.0:5000 "app:app"
```

**Production ù Windows (Waitress):**
```bash
pip install waitress
waitress-serve --port=5000 app:app
```

Open **http://localhost:5000** ù log in with the default credentials:

| Username | Password |
|---|---|
| `admin` | `Admin123!` |

**Change the password immediately after first login.**

---

## 9. First Steps After Install

Follow these steps to get your first display showing content:

1. **Change the admin password** ù click your username ? Profile ? Change Password
2. **Upload media** ù Media ? Add Media (images, videos, or web URLs)
3. **Create a playlist** ù Playlists ? New Playlist ? add your media items
4. **Add a display** ù Displays ? Add New Display (or use browser/native client self-registration)
5. **Assign the playlist** ù open the Display detail page and select your playlist
6. **Open the player** ù navigate to `http://your-server/display/<token>` on the display device

---

## 10. Running Behind a Reverse Proxy (nginx / Caddy)

If you put AISignX behind nginx or Caddy for HTTPS, set in `config.py`:
```python
TRUST_PROXY = True
PREFERRED_URL_SCHEME = 'https'
```

**nginx configuration:**
```nginx
server {
    listen 443 ssl;
    server_name signage.example.com;

    ssl_certificate     /etc/ssl/certs/signage.crt;
    ssl_certificate_key /etc/ssl/private/signage.key;

    location / {
        proxy_pass         http://127.0.0.1:5000;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;

        # Required for SSE (live push to displays ù do not remove)
        proxy_buffering    off;
        proxy_cache        off;
        proxy_read_timeout 3600s;
    }
}
```

For a full production deployment guide including systemd, SSL certificates, and Cloudflare, see [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md).

---

## 11. Native Display Clients

The Electron (Windows/Linux) and Android native clients provide kiosk-mode display with auto-start and crash recovery.

See [CLIENTS.md](CLIENTS.md) for build and install instructions.

For browser-based access (no install required), see [BROWSER_ACCESS.md](BROWSER_ACCESS.md).

---

## Related Documentation

| Document | What it covers |
|---|---|
| [USER_GUIDE.md](USER_GUIDE.md) | Day-to-day: media, playlists, schedules, emergency |
| [ADMIN_GUIDE.md](ADMIN_GUIDE.md) | Users, displays, groups, API tokens, settings |
| [CLIENTS.md](CLIENTS.md) | Building and deploying native clients |
| [BROWSER_ACCESS.md](BROWSER_ACCESS.md) | Browser-based kiosk display |
| [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) | nginx, systemd, SSL, Cloudflare |
| [EMERGENCY_BROADCAST.md](EMERGENCY_BROADCAST.md) | Emergency alert system |
| [PLUGINS.md](PLUGINS.md) | Built-in and custom plugins |
| [API.md](API.md) | REST API reference |
| [UPGRADE.md](UPGRADE.md) | Upgrading from a previous version |
| [TROUBLESHOOTING.md](TROUBLESHOOTING.md) | Common problems and fixes |