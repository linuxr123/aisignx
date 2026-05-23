# AISignX documentation

Welcome. This page is the **entry point** for all project documentation.

Detailed guides live in [`server/docs/`](../server/docs/). Paths below are relative to the repository root.

---

## New here?

1. Read the [main README](../README.md) — what AISignX is and a one-minute install.
2. Follow **[Getting started](../server/docs/GETTING_STARTED.md)** — FFmpeg, venv, database, first login.
3. Walk through **[First steps](FIRST_STEPS.md)** — first playlist on a display.
4. Skim **[Features](../server/docs/FEATURES.md)** — what is shipped vs planned.
5. See **[Architecture](ARCHITECTURE.md)** — how server, clients, and SSE fit together.

---

## By role

### Overview

| Document | Description |
|----------|-------------|
| [FIRST_STEPS.md](FIRST_STEPS.md) | First playlist on a display after install |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System design, mermaid diagrams, data flow |
| [images/README.md](images/README.md) | Screenshot placeholders for README |

### Install & run the server

| Document | Description |
|----------|-------------|
| [GETTING_STARTED.md](../server/docs/GETTING_STARTED.md) | Install, config wizard, database, first display |
| [SERVER_HTTP_ONLY_or_HTTPS_ONLY_Version2.md](../server/docs/SERVER_HTTP_ONLY_or_HTTPS_ONLY_Version2.md) | HTTP direct vs HTTPS behind a proxy |
| [PRODUCTION_DEPLOYMENT.md](../server/docs/PRODUCTION_DEPLOYMENT.md) | nginx, systemd, SSL, Gunicorn/Waitress |
| [DEPLOY_WINDOWS.md](../server/docs/DEPLOY_WINDOWS.md) | Windows service, IIS/Caddy |
| [DEPLOY_LINUX.md](../server/docs/DEPLOY_LINUX.md) | Linux host, Docker Compose |
| [TROUBLESHOOTING.md](../server/docs/TROUBLESHOOTING.md) | Common problems |
| [UPGRADE.md](../server/docs/UPGRADE.md) | Upgrading an existing install |

### Operate signage (content & schedules)

| Document | Description |
|----------|-------------|
| [USER_GUIDE.md](../server/docs/USER_GUIDE.md) | Media, playlists, schedules, emergency |
| [PLAYLISTS_MEDIA.md](../server/docs/PLAYLISTS_MEDIA.md) | Deep dive: media library & playlists |
| [SCHEDULES.md](../server/docs/SCHEDULES.md) | Scheduling displays and groups |
| [DISPLAYS.md](../server/docs/DISPLAYS.md) | Displays, registration, commands |
| [EMERGENCY_BROADCAST.md](../server/docs/EMERGENCY_BROADCAST.md) | Emergency templates and broadcast |
| [PLUGINS.md](../server/docs/PLUGINS.md) | Built-in plugins (clock, weather, etc.) |
| [BROWSER_ACCESS.md](../server/docs/BROWSER_ACCESS.md) | Browser-only player (no app) |

### Administration & security

| Document | Description |
|----------|-------------|
| [ADMIN_GUIDE.md](../server/docs/ADMIN_GUIDE.md) | Users, roles, tenants, settings |
| [MULTI_TENANCY.md](../server/docs/MULTI_TENANCY.md) | Tenants, permissions, isolation (developers) |
| [OPERATIONS.md](../server/docs/OPERATIONS.md) | Backups, audit, rate limits, health |
| [API.md](../server/docs/API.md) | REST API reference |
| [api-tool-usage.md](../server/docs/api-tool-usage.md) | In-app API tools page |

### Display clients

| Document | Description |
|----------|-------------|
| [CLIENTS.md](../server/docs/CLIENTS.md) | Electron & Android setup and deploy |
| [clients/README.md](../clients/README.md) | Client folder overview & build scripts |
| [WINDOWS_CLIENT_PACKAGING.md](../server/docs/WINDOWS_CLIENT_PACKAGING.md) | Windows installer / signing notes |
| [CLIENT_COMMAND_PROTOCOL.md](../server/docs/CLIENT_COMMAND_PROTOCOL.md) | Reload, reboot, update commands |

### Developers & integrators

| Document | Description |
|----------|-------------|
| [FEATURES.md](../server/docs/FEATURES.md) | Feature catalog (✅ / 🟡 / 🔲) |
| [MULTI_TENANCY.md](../server/docs/MULTI_TENANCY.md) | Tenant model and scanner rules |
| [CONTRIBUTING.md](../CONTRIBUTING.md) | How to contribute (repo root) |
| [CHANGELOG.md](../CHANGELOG.md) | Version history (repo root) |
| [THIRD_PARTY_LICENSES.md](../THIRD_PARTY_LICENSES.md) | Dependency licenses |

### Database & migrations

| Document | Description |
|----------|-------------|
| [DB Migration.md](../server/docs/DB%20Migration.md) | Database migration notes |
| [UPGRADE_PHASE1.md](../server/docs/UPGRADE_PHASE1.md) | Phase 1 upgrade path |

---

## Quick links

| Task | Go to |
|------|--------|
| Run server locally | [GETTING_STARTED.md](../server/docs/GETTING_STARTED.md) |
| Move media to another drive | [OPERATIONS.md — Storage](../server/docs/OPERATIONS.md#storage) |
| Enable HTTPS | [generate_config.py](../server/generate_config.py) + [SERVER_HTTP…](../server/docs/SERVER_HTTP_ONLY_or_HTTPS_ONLY_Version2.md) |
| Build Electron/APK | [clients/README.md](../clients/README.md) |
| REST integration | [API.md](../server/docs/API.md) |
| AGPL obligations | [LICENSE](../LICENSE) |

---

## Repository map (documentation only)

```text
docs/README.md          ← you are here (index)
server/docs/*.md        ← full guides
clients/README.md       ← player apps
README.md               ← project overview & quick start
```

If a link 404s on GitHub, check that the file exists under `server/docs/` on your branch.
