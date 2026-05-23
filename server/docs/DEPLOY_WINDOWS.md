# AISignX on Windows (Production)

This guide covers running the AISignX server as a long-lived service on a
Windows host. Windows is the primary target for the server; Linux is also
supported but documented separately in `DEPLOY_LINUX.md`.

The server pairs with two client surfaces:
- **Windows displays** — Electron player or browser kiosk
- **Android displays** — built-in Chromium WebView pointed at `/display/k/<token>`

---

## 1. Host requirements

- Windows 10/11 Pro or Server 2019/2022, 64-bit
- 4 GB RAM minimum (8 GB recommended once you have ~20+ active displays)
- 20 GB free on the drive that will hold uploads
- Outbound HTTPS for Let's Encrypt / package downloads (optional, only if you
  want to fetch updates and certs from this host)
- A static LAN IP **or** a stable hostname your displays can reach

The server is a single Python process with a SQLite database — no SQL Server,
no IIS, no domain join required.

---

## 2. Install Python and dependencies

1. Install **Python 3.13 (64-bit)** from https://python.org. Tick
   "Add python.exe to PATH" during install.
2. Open an elevated **PowerShell** in the project folder:
   ```powershell
   cd C:\Apps\AISignXV2
   py -3.13 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   pip install waitress
   ```
3. Optional (only needed for HTML/web-page plugins):
   ```powershell
   python -m playwright install chromium
   ```
4. Optional (only needed for video/image transcoding plugins):
   - Install **ffmpeg** from https://www.gyan.dev/ffmpeg/builds/ and add the
     `bin\` folder to `PATH`.

---

## 3. Configure secrets

Create `C:\Apps\AISignXV2\.env`:
```
AISIGNX_SECRET_KEY=<paste 64+ random chars here>
AISIGNX_PREFERRED_URL_SCHEME=https
AISIGNX_TRUST_PROXY_HOPS=1
```
Generate a key:
```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

`config.py` reads these on startup. Never commit `.env`.

---

## 4. First boot

```powershell
.\.venv\Scripts\Activate.ps1
python app.py
```
Visit `http://localhost:5000` and log in with the bootstrapped admin
account printed on first run. Confirm:

- `/dashboard` renders
- `/admin/system-health` shows disk/jobs/displays
- `/admin/audit-retention` and `/admin/proof-of-play` open

Then stop the dev server (Ctrl+C) — production will run it under Waitress.

---

## 5. Run as a Windows Service (NSSM)

The simplest production wrapper is **NSSM** (https://nssm.cc/). It turns any
executable into a real Windows Service with restart-on-crash and standard
event-log integration.

1. Download NSSM and place `nssm.exe` somewhere on `PATH` (e.g.
   `C:\Tools\nssm.exe`).
2. From an elevated PowerShell:
   ```powershell
   nssm install AISignX "C:\Apps\AISignXV2\.venv\Scripts\python.exe" `
       "-m waitress --host=0.0.0.0 --port=5000 app:app"
   nssm set AISignX AppDirectory  C:\Apps\AISignXV2
   nssm set AISignX AppStdout     C:\Apps\AISignXV2\logs\service.out.log
   nssm set AISignX AppStderr     C:\Apps\AISignXV2\logs\service.err.log
   nssm set AISignX Start         SERVICE_AUTO_START
   nssm set AISignX AppEnvironmentExtra `
       AISIGNX_SECRET_KEY=<your-key> `
       AISIGNX_PREFERRED_URL_SCHEME=https `
       AISIGNX_TRUST_PROXY_HOPS=1
   nssm start AISignX
   ```
3. Confirm in `services.msc` that **AISignX** is running.

To update later: `nssm stop AISignX`, replace files / `pip install -r requirements.txt`,
then `nssm start AISignX`.

A starter script that performs the install above is provided at
`scripts/install_windows_service.ps1`.

---

## 6. Reverse proxy + TLS (recommended)

Waitress speaks plain HTTP. For LAN-only deployments that's fine; for any
internet-reachable install, terminate TLS in front of it:

- **IIS** with the URL Rewrite + ARR modules — proxy `https://signage.example.com/`
  to `http://127.0.0.1:5000/`. Forward `X-Forwarded-For` and
  `X-Forwarded-Proto`.
- **Caddy for Windows** — one-line reverse proxy with automatic Let's Encrypt:
  ```
  signage.example.com {
      reverse_proxy 127.0.0.1:5000
  }
  ```

Set `AISIGNX_TRUST_PROXY_HOPS=1` so Flask believes the proxy's
`X-Forwarded-*` headers.

---

## 7. Backups

The app exposes one-click backup at `/admin/backups`:

- Each backup is a single `.zip` containing a consistent online SQLite
  snapshot, the uploads tree, and a manifest with row counts.
- Files land in `C:\Apps\AISignXV2\backups\`.
- Schedule a Task Scheduler job to copy that folder to network/offsite
  storage nightly:
  ```powershell
  robocopy C:\Apps\AISignXV2\backups \\nas\backups\AISignX /MIR /R:1 /W:5
  ```

Restore is performed from the same UI (uploads a `.zip`, swaps files,
restarts the service).

---

## 8. Display clients

### Windows displays

Two options, both work fully offline once they have content:

1. **Electron player** (preferred — supports `reboot`/`update`/`reload`
   commands pushed from `/displays/<id>` or the group-level Command button).
2. **Browser kiosk** — open Edge/Chrome to `/display/k/<token>` in
   kiosk mode. PoP and policy enforcement still work.

### Android displays

Open the system browser (or any WebView shell) to:
```
https://signage.example.com/display/k/<token>
```
Add it to the homescreen / configure as a kiosk app. The player batches
Proof-of-Play locally and resyncs when the network returns, so spotty
WiFi will not lose play counts.

---

## 9. Health checks

- `GET /healthz` — public, unauthenticated JSON probe. Returns
  `200 {"status":"ok"}` when the database is reachable, `503` otherwise.
  Use this for IIS/Caddy/load-balancer health checks, UptimeRobot,
  Windows service watchdogs, and Docker/K8s liveness.
- `GET /static/sw.js` — cheap static liveness probe (no DB hit).
- `GET /admin/system-health` — superadmin one-pager covering disk usage,
  background-job queues, display online ratio, and append-only log totals.
- Audit retention sweep runs daily; control or trigger from
  `/admin/audit-retention`.

---

## 10. Upgrades

1. `nssm stop AISignX`
2. Take a backup (`/admin/backups`).
3. Pull/copy the new code into `C:\Apps\AISignXV2`.
4. `pip install -r requirements.txt`
5. `nssm start AISignX`
6. Open `/admin/system-health` and confirm everything is green.
