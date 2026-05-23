# AISignX

A self-hosted digital signage platform. Manage displays, playlists, media, and schedules from a central web admin. Displays can use a web browser, the Electron kiosk client (Windows / Linux), or the Android app — all managed from one server with live push updates.

**License:** [GNU AGPL-3.0-or-later](LICENSE) — see [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) for dependency licenses.

---

## Repository layout

```
aisignx/
├── server/                 Flask app, admin UI, API, plugins, docs
│   ├── app.py
│   ├── config.example.py   Template — copy to config.py (gitignored)
│   ├── docs/
│   ├── templates/
│   ├── static/
│   └── plugins/
├── clients/
│   ├── electron-client/    Windows / Linux kiosk (Electron)
│   └── android-client/     Android WebView player
├── build_clients_windows.ps1
├── build_clients_linux.sh
├── .env.example            Environment variable template
├── LICENSE                 GNU AGPL-3.0-or-later
└── THIRD_PARTY_LICENSES.md Dependency license summary
```

---

## Requirements

| Component | Requirement |
|-----------|-------------|
| **Server** | Python 3.10+ (3.11–3.12 recommended), pip, FFmpeg |
| **Server (optional)** | Docker & Docker Compose |
| **Electron client** | Node.js 18+ LTS, npm |
| **Android client** | JDK 17, Android SDK (Android Studio recommended) |
| **OS** | Windows, Linux, or macOS for development |

See [server/docs/GETTING_STARTED.md](server/docs/GETTING_STARTED.md) for detailed prerequisites.

---

## Quick start (new developer)

### 1. Clone and enter the repo

```bash
git clone https://github.com/yourorg/aisignx.git
cd aisignx
```

### 2. Configure secrets

**Option A — environment variables (recommended for Docker/production):**

```bash
cp .env.example server/.env
# Edit server/.env and set AISIGNX_SECRET_KEY to a long random value
```

**Option B — local Python config:**

```bash
cd server
cp config.example.py config.py
# Or run: python generate_config.py  (creates config.py with a random dev secret)
```

Generate a secret key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

> `config.py` and `.env` are **gitignored**. Never commit real secrets.

### 3. Install and run the server

**Windows** (from repo root):

```powershell
powershell -ExecutionPolicy Bypass -File server\install_windows.ps1
cd server
.\.venv\Scripts\Activate.ps1
python app.py
```

**Linux / macOS:**

```bash
chmod +x server/install_linux.sh && ./server/install_linux.sh
cd server
source .venv/bin/activate
python app.py
```

Open http://localhost:5000 — default login: `admin` / `Admin123!` (**change immediately**).

### 4. Build display clients (optional)

From the **repo root**:

```powershell
# Windows — builds Electron + Android by default, bumps patch version
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Help
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1
```

```bash
# Linux
chmod +x build_clients_linux.sh
./build_clients_linux.sh --help
./build_clients_linux.sh
```

Installers are copied to `server/static/clients/` for the admin **Downloads** page. Binaries are gitignored; only `client_versions.json` is tracked as a template.

**Android release signing:** copy `clients/android-client/keystore.properties.example` to `keystore.properties` (gitignored) and add your keystore. Debug builds work without it.

See [clients/README.md](clients/README.md) and [server/docs/CLIENTS.md](server/docs/CLIENTS.md).

### 5. Docker (alternative)

```bash
cp .env.example server/.env
# Edit server/.env — set AISIGNX_SECRET_KEY

cd server
docker compose up -d --build
```

---

## Local development vs. what Git publishes

Your working folder is a **full dev environment**. Git is configured so you can keep building and testing locally without polluting the public repo:

| Stays on your machine (gitignored) | Published in Git |
|-------------------------------------|------------------|
| `server/config.py`, `.env` | `config.example.py`, `.env.example` |
| `server/uploads/`, `*.db`, logs | Source code, templates, plugins |
| `node_modules/`, `dist/`, build outputs | `package.json`, client source |
| Built `.exe`, `.apk`, `.AppImage`, `.deb` | Build scripts, `client_versions.json` |
| Android keystore & `keystore.properties` | `keystore.properties.example` |
| `RESUME_CONTEXT.md`, IDE caches | Documentation, LICENSE, THIRD_PARTY_LICENSES |

Run `git status` before pushing to confirm no secrets or build artifacts are staged.

---

## Documentation

All guides live under **`server/docs/`**:

| Document | What it covers |
|----------|----------------|
| [GETTING_STARTED.md](server/docs/GETTING_STARTED.md) | Installation, configuration, first steps |
| [USER_GUIDE.md](server/docs/USER_GUIDE.md) | Media, playlists, schedules, emergency |
| [ADMIN_GUIDE.md](server/docs/ADMIN_GUIDE.md) | Users, tokens, system settings |
| [CLIENTS.md](server/docs/CLIENTS.md) | Electron and Android clients |
| [API.md](server/docs/API.md) | REST API reference |
| [MULTI_TENANCY.md](server/docs/MULTI_TENANCY.md) | Tenants, permissions |
| [DEPLOY_WINDOWS.md](server/docs/DEPLOY_WINDOWS.md) | Production on Windows |
| [DEPLOY_LINUX.md](server/docs/DEPLOY_LINUX.md) | Production on Linux |

---

## License

AISignX is **free software** licensed under the
[GNU Affero General Public License v3.0 or later](LICENSE) (AGPL-3.0-or-later).

**What that means in practice:**

- You may use, modify, and redistribute AISignX.
- If you **distribute** the software (including modified versions), you must
  provide **corresponding source code** under the same license.
- If you run a **modified version** as a **network service** (users interact
  with it over a network), you must offer those users the **source code** of
  your modified version (AGPL section 13).

**Binary releases** (Windows installer, APK, etc.): source for each release
is the matching Git tag in this repository. See [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md)
for dependency licenses.

**Trademark:** “AISignX” names and branding are not granted by the license;
forks should use their own product name unless explicitly permitted.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflow and pre-push checks.

---

## Security

Do not commit API keys, passwords, or signing keystores. Report security issues as described in [SECURITY.md](SECURITY.md).
