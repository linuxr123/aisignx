# AISignX

**Self-hosted digital signage** — one server, a web admin, and players for browsers, Windows/Linux kiosks, and Android.

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL%20v3-blue.svg)](LICENSE)

---

## What this is

AISignX lets you:

- Upload **images, videos, and web pages** into a media library
- Build **playlists** (transitions, timing, plugins, smart rules)
- Schedule content on **displays** and **display groups**
- Push **live updates** to screens (no manual refresh)
- Run **multiple tenants** (customers/workspaces) from one install
- Send **emergency broadcasts** and review **proof of play**

You host the server yourself. Display devices connect with a browser or a native kiosk app.

> **Note:** There are no pre-built [GitHub Releases](https://github.com/linuxr123/aisignx/releases) yet. Install the server from source; build display clients locally when you need them (see [Clients](#display-clients-optional)).

---

## Use cases

| Scenario | How AISignX fits |
|----------|------------------|
| **Retail / lobby screens** | Playlists + schedules; group displays by location |
| **Internal comms** | Webpage URLs, RSS/weather plugins, emergency override |
| **Multi-customer hoster** | Tenant isolation, roles, per-tenant branding |
| **Kiosk / dedicated device** | Electron (Windows/Linux) or Android full-screen player |
| **Quick trial** | Browser player at `/display/<token>` — no app install |

---

## One-minute quick start

**Goal:** Admin UI running on your machine in a few commands.

```bash
git clone https://github.com/linuxr123/aisignx.git
cd aisignx
```

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

Open **http://localhost:5000** → login `admin` / `Admin123!` → **change the password immediately**.

**Next steps:** [Documentation](docs/README.md) · [First-time setup (detailed)](server/docs/GETTING_STARTED.md)

---

## Architecture overview

```text
                    ┌─────────────────────────────────────┐
                    │  Admin browsers (HTTPS optional)     │
                    └──────────────────┬──────────────────┘
                                       │
                    ┌──────────────────▼──────────────────┐
                    │  Reverse proxy (optional)            │
                    │  nginx / Caddy / IIS — TLS :443      │
                    └──────────────────┬──────────────────┘
                                       │ HTTP
                    ┌──────────────────▼──────────────────┐
                    │  AISignX server (Flask)              │
                    │  • Admin UI + REST API               │
                    │  • SQLite DB, uploads, plugins     │
                    │  • SSE live push to displays         │
                    └──────────┬────────────┬─────────────┘
                               │            │
              ┌────────────────┘            └────────────────┐
              ▼                                              ▼
    Browser player                              Native clients
    /display/<token>                            Electron / Android
```

| Part | Location | Role |
|------|----------|------|
| **Server** | `server/` | Flask app, templates, API, plugins, tenant DB |
| **Clients** | `clients/` | Electron kiosk + Android WebView player |
| **Build** | `build_clients_*.ps1` / `.sh` | Package clients → `server/static/clients/` |
| **Docs** | [`docs/README.md`](docs/README.md) + `server/docs/` | Guides and reference |

**HTTPS:** Terminate TLS at a reverse proxy; AISignX stays on `http://127.0.0.1:5000`. Use `python generate_config.py --mode https` in `server/`. See [HTTP vs HTTPS](server/docs/SERVER_HTTP_ONLY_or_HTTPS_ONLY_Version2.md).

---

## Requirements

| Component | Requirement |
|-----------|-------------|
| **Server** | Python 3.10+, FFmpeg, 1–2 GB RAM |
| **Production** | Waitress/Gunicorn + optional nginx/Caddy |
| **Electron builds** | Node.js 18+ |
| **Android builds** | JDK 17, Android SDK |

Full list: [Getting started — requirements](server/docs/GETTING_STARTED.md).

---

## Configuration (HTTP vs HTTPS)

Interactive setup (recommended):

```bash
cd server
python generate_config.py --interactive
```

| Mode | When to use |
|------|-------------|
| `http` | LAN, dev, direct port 5000 |
| `https` | Production behind nginx/Caddy/IIS |

Details: [Deploy modes](server/docs/GETTING_STARTED.md#6-generate-config) · [Production deployment](server/docs/PRODUCTION_DEPLOYMENT.md)

---

## Display clients (optional)

Native apps are **built from source** in this repo (no release binaries on GitHub yet).

```powershell
# Windows (repo root)
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Help
```

```bash
# Linux (repo root)
./build_clients_linux.sh --help
```

Output goes to `server/static/clients/` for the admin **Downloads** page. See [Clients](clients/README.md) and [server/docs/CLIENTS.md](server/docs/CLIENTS.md).

---

## Documentation

**Start here:** [**Documentation index**](docs/README.md) — maps every guide in `server/docs/`.

| Audience | Start with |
|----------|------------|
| New installer | [GETTING_STARTED.md](server/docs/GETTING_STARTED.md) |
| Day-to-day operator | [USER_GUIDE.md](server/docs/USER_GUIDE.md) |
| Tenant / system admin | [ADMIN_GUIDE.md](server/docs/ADMIN_GUIDE.md) |
| Developers / integrators | [API.md](server/docs/API.md), [MULTI_TENANCY.md](server/docs/MULTI_TENANCY.md) |
| Feature list | [FEATURES.md](server/docs/FEATURES.md) |

---

## Repository layout

```text
aisignx/
├── docs/                   Documentation index (you are here in README)
├── server/                 Flask application
├── clients/                Electron + Android players
├── CHANGELOG.md
├── CONTRIBUTING.md
└── LICENSE                 AGPL-3.0-or-later
```

---

## Docker

```bash
cp .env.example server/.env
# Set AISIGNX_SECRET_KEY and AISIGNX_DEPLOY_MODE=https if behind a proxy

cd server
docker compose up -d --build
```

See [DEPLOY_LINUX.md](server/docs/DEPLOY_LINUX.md).

---

## Contributing & security

- [Contributing](CONTRIBUTING.md) — workflow, AGPL, pre-push checks  
- [Security](SECURITY.md) — reporting vulnerabilities  
- [Changelog](CHANGELOG.md) — version history  
- [Third-party licenses](THIRD_PARTY_LICENSES.md)

---

## License

AISignX is free software under the [GNU Affero General Public License v3.0 or later](LICENSE).

If you distribute or run a modified version as a network service, AGPL requires offering corresponding source to users. Forks should use their own product name unless permitted.
