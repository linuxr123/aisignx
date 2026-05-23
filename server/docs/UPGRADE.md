# AISignX — Upgrade Guide

How to upgrade from a previous version.

---

## Before Every Upgrade

- [ ] Back up the database: `cp signage.db signage.db.bak-$(date +%Y%m%d)` (Linux) or copy `signage.db` in File Explorer (Windows)
- [ ] Back up the `uploads/` folder
- [ ] Back up `config.py`
- [ ] Schedule the upgrade during low-traffic hours — the server will be briefly offline

---

## Step-by-Step Upgrade

```bash
# 1. Pull the latest code
git pull origin main

# 2. Activate your virtual environment
# Windows:
venv\Scripts\activate
# Linux / macOS:
source venv/bin/activate

# 3. Install any new Python dependencies
pip install -r requirements.txt

# 4. Update Playwright if needed
playwright install chromium

# 5. Run database migrations
python migration.py

# 6. Restart the server
# Development:
python app.py

# Production (Linux systemd):
sudo systemctl restart aisignx

# Production (Windows NSSM):
nssm restart AISignX
```

---

## After Upgrading

### Clear Display Caches

After a server upgrade the service worker version is bumped automatically. Displays detect this and reload within ~30 seconds. If a display does not update:

1. Hard-refresh the browser: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac)
2. Or in DevTools ? Application ? Service Workers ? click **Unregister** ? reload

### Update Native Client Packages

If the upgrade includes new Electron or Android client builds:

1. Build the new packages — see [CLIENTS.md](CLIENTS.md)
2. Copy them to `static/clients/`
3. Go to **Downloads** in the admin ? **Manifest Editor** ? bump the relevant version numbers ? **Save**
4. **Ways for clients to pick up the new build:**
   - **Passive:** Running native clients auto-detect the update on their next check.
   - **Display auto-update:** Enable **Auto-update client** on a display. Android checks immediately when the player receives that setting, then every 15 minutes.
   - **Push:** Open each Display detail page and click **Update Client**. Electron Windows updates silently. Android updates silently only when provisioned as Device Owner; otherwise Android may show the system install approval prompt. See [ADMIN_GUIDE.md ? Pushing Commands to Displays](ADMIN_GUIDE.md#7b-pushing-commands-to-displays).

For Android native changes such as Device Owner support, install the new APK
manually once on devices that are still running an older build. After Android
client `1.4.6+` is installed and the device is Device Owner, future APK updates
can be unattended.

---

## Rolling Back

```bash
git checkout <previous-tag-or-commit>
pip install -r requirements.txt
# Restore the database backup
cp signage.db.bak-20250101 signage.db
sudo systemctl restart aisignx
```

For migrations that added new columns, restoring the database backup automatically reverts the schema.

---

## Troubleshooting Upgrades

| Problem | Fix |
|---|---|
| `python migration.py` fails with alembic errors | Check `migrations/versions/` for conflicting scripts. Delete any auto-generated duplicates and re-run |
| Displays not updating after upgrade | Bump `CACHE_VER` in `static/sw.js` manually and restart |
| Playwright errors after upgrade | Re-run `playwright install chromium` |
| 500 errors after upgrade | Check server logs: `journalctl -u aisignx -n 100` — usually a missing dependency or config change |
| Port already in use | Kill the old process: `fuser -k 5000/tcp` (Linux) |

---

## Version History

Update this table as you release new versions.

| Version | Date | Notes |
|---|---|---|
| 1.0.0 | 2025-01-01 | Initial release |
| 1.1.0 | 2026-04-25 | Clock plugin theme system (drop-in `themes/` folders, `options_from` schema feature). Weather radar rebuilt on RainViewer + OSM. Display detail page **Reload / Reboot App / Update Client** push commands (new `POST /api/displays/<id>/command` endpoint and SSE `command` event). Electron Windows client moved to per-user install (no UAC). Silent admin-pushed auto-update via Windows Task Scheduler with full logging. Diagnostic hotkeys (Ctrl+Alt+L / D / Q). Downloads page Manifest Editor and BOM-tolerant `client_versions.json` parsing. Electron client now starts fullscreen-kiosk from launch with no windowed flash. |
| 1.2.0 | 2026-05-16 | Offline-first player hardening, server-time sync calibration, drift watchdog recovery, opt-in display diagnostics, central Display Diagnostics page, uniform filters/bulk actions across admin list pages, tenant terminology in UI, bulk roles and custom role fixes, video duration probing with `0` = full video, media/playlist **Use video length** reset controls, idle cursor hiding, Android WebView placeholder mitigation, Android long-press unlock fallback, and Android Device Owner receiver for unattended APK updates. |