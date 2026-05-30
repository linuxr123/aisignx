# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project will use [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
when tagged releases begin.

> **GitHub Releases:** Not published yet. Clone `main`, install the server from source,
> and build display clients locally when needed. See [README](README.md).

---

## [Unreleased]

### Documentation & repository
- Root documentation index at [`docs/README.md`](docs/README.md)
- Restructured README: use cases, one-minute quick start, architecture diagram
- **Product-style README:** TOC, features, mermaid architecture, screenshots section, project status
- Added `docs/ARCHITECTURE.md`, `docs/FIRST_STEPS.md`, `docs/images/` for screenshots
- Added `CHANGELOG.md` (this file)
- AGPL-3.0 licensing, `CONTRIBUTING.md`, `SECURITY.md`, `THIRD_PARTY_LICENSES.md`
- **Storage relocation:** documented global/per-tenant paths in `OPERATIONS.md`, `ARCHITECTURE.md`, `GETTING_STARTED.md`, `MULTI_TENANCY.md`, `FEATURES.md`

### Server setup
- Config wizard: `server/generate_config.py --interactive`
- Deploy modes: `http` (direct) and `https` (reverse proxy) via `server/deploy_modes.py`
- `.env.example` and `config.example.py` templates

### Storage & disk management
- **Global upload root** — superadmin setting `disk.upload_root` (System Settings) with optional migration of all tenant folders (`d1/`, `d2/`, …) when the path changes
- **Per-tenant storage path** — superadmin-only on **Tenant Management** → Edit tenant → **Storage location**; moves only that tenant’s media tree
- **Server folder browser** — inline drive/folder picker in the tenant editor (`GET /api/system/path-browser`); lists paths on the **server**, not the admin’s PC
- New module `server/upload_paths.py` — path resolution, validation, and migration helpers
- `Domain.storage_root_path` column (auto-added at boot via bootstrap)
- `storage.py` resolves media under global or per-tenant custom roots while DB paths stay `d{id}/…`

### Client offline & kiosk unlock
- **Offline media playback** — Electron now marks the configured HTTP server origin as a trusted secure origin so the player's service worker (and its media cache) registers on plain-HTTP LAN deployments; previously the SW silently never registered over HTTP, so nothing was cached and videos failed the moment the network dropped
- **Video stall watchdog** (`display_player.js`, all clients) — a playing video that stops making progress (network/server dropped mid-stream, partial cache) now advances after ~6s instead of holding the slide for its full duration cap; fixes "video plays too long" and long gaps between media when offline
- **PIN unlock minimizes the client** — entering the unlock PIN now minimizes the kiosk so a technician can reach the desktop. It stays minimized until the OS is idle for 5 minutes **or** the user brings it back, then it re-asserts kiosk fullscreen and re-locks automatically
  - Electron: `unlock-minimize` IPC + `powerMonitor` idle polling; restores on idle or on window restore/focus
  - Android: drops the lock task and backgrounds the app; restores + re-locks on `onResume` or after a 5-minute idle timer
  - New preload bridge `signage.unlockMinimize()` / `signage.onRelock()` and `window.AISignXRelock()` / `AISignXNative.unlockMinimize()` hooks

### Android provisioning & signed builds
- **Release Device Owner** command + display-detail button (`release_device_owner`) — calls `DevicePolicyManager.clearDeviceOwnerApp()` so an Android kiosk can be un-provisioned/uninstalled without a factory reset; added to the single, bulk, and group command allowlists
- **Signed release APK build** — `build_clients_windows.ps1 -Release` / `build_clients_linux.sh --release` run `assembleRelease` using `clients/android-client/keystore.properties`; added `keystore.properties.example` and documented Android's same-key update rule
- Build scripts now write `package.json` / `build.gradle.kts` / `client_versions.json` as UTF-8 **without a BOM** (the previous BOM broke electron-builder's JSON parser)
- Docs: Android signing + Device Owner provisioning/undo (`clients/android-client/README.md`), command protocol `release_device_owner`

### Repository layout
- Monorepo: `server/` (Flask app), `clients/` (Electron + Android)
- Root build scripts → `server/static/clients/`
- `.gitignore` excludes secrets, databases, uploads, and build artifacts

### Fixes (recent)
- Tenant Management: script load order (Bootstrap) and inline server folder browser
- Emergency schedules tab: loading and new-template flow for empty tenants
- Superadmin default tenant: `slug=default`
- Proof of Play: tenant-scoped admin and filters
- Android signing: optional `keystore.properties` (no secrets in repo)

### Initial open-source scope (main branch)
- Multi-tenant digital signage: media, playlists, schedules, displays, groups
- Live SSE push to players; browser, Electron, and Android clients (build from source)
- Plugins, emergency broadcast, proof of play, audit, backups, API tokens
- Full guides in `server/docs/` — indexed from `docs/README.md`

[Unreleased]: https://github.com/linuxr123/aisignx
