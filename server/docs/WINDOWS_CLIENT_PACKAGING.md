# Windows Display Client — Packaging & Auto-Update

This document describes how to build the AISignX Windows Display Client
(Electron) so that the **Update Client** button on the server (and its
group/bulk equivalents) actually installs a new version.

The server side already supports the protocol: the SSE `command` event
with `{"action":"update"}` is delivered to the connected display, audited
as `display.command` / `displays.bulk_command`, and the displays page
shows the new `app_version` once the player phones home again. All that's
left is making sure the client knows what to do with the `update` event
and where to fetch new builds from.

---

## 1. Recommended toolchain

- **electron-builder** for installer generation
- **electron-updater** for in-app updates (uses the same metadata that
  electron-builder produces)
- **NSIS** target (default on Windows) — produces a single signed
  `.exe` installer
- **Code signing certificate** (EV strongly recommended; SmartScreen
  reputation is faster to warm up)

The `update` action calls `autoUpdater.checkForUpdatesAndNotify()` /
`quitAndInstall()`; with the NSIS target this becomes a silent "download
new build → close player → install → relaunch" cycle.

---

## 2. electron-builder configuration

In the player project's `package.json`:

```jsonc
{
  "name":    "aisignx-display",
  "version": "1.0.0",
  "build": {
    "appId":       "com.example.aisignx.display",
    "productName": "AISignX Display",
    "win": {
      "target":            "nsis",
      "icon":              "build/icon.ico",
      "publisherName":     "Your Company, Inc.",
      "certificateFile":   "build/codesign.pfx",
      "certificatePassword": "${env.AISIGNX_CSC_KEY_PASSWORD}",
      "signAndEditExecutable": true,
      "verifyUpdateCodeSignature": true
    },
    "nsis": {
      "oneClick":          false,
      "perMachine":        true,
      "allowToChangeInstallationDirectory": true,
      "createDesktopShortcut": true,
      "createStartMenuShortcut": true,
      "shortcutName":      "AISignX Display"
    },
    "publish": [
      {
        "provider": "generic",
        "url":      "https://signage.example.com/static/updates/win/",
        "channel":  "latest"
      }
    ]
  }
}
```

> Replace `signage.example.com` with the actual hostname of your AISignX
> server. The auto-updater downloads from there, so it must be reachable
> from every display.

Build:
```powershell
$env:AISIGNX_CSC_KEY_PASSWORD = '...'
npx electron-builder --win --x64
```
Output ends up in `dist/`:
- `AISignX Display Setup 1.0.0.exe`  — full installer
- `AISignX Display Setup 1.0.0.exe.blockmap`
- `latest.yml`                        — update manifest

---

## 3. Hosting the update feed on the AISignX server

Serve the three files from `static/updates/win/` on the server:

```
static/
└── updates/
    └── win/
        ├── latest.yml
        ├── AISignX Display Setup 1.0.0.exe
        └── AISignX Display Setup 1.0.0.exe.blockmap
```

Flask already serves `static/*` cache-friendly with appropriate headers,
so no new routes are required. To publish a new build:

1. Run `electron-builder` as above.
2. Copy the three files into `static/updates/win/`, **overwriting**
   `latest.yml` and adding the new `Setup x.y.z.exe` (older installers
   may be left in place for rollback or pruned by a scheduled task).
3. Operators click **Update Client** on `/display/<id>` (or the bulk
   button on `/displays/`), which fans out the SSE `update` command.
4. Each player calls `autoUpdater.checkForUpdates()`, sees the newer
   version in `latest.yml`, downloads, then `quitAndInstall()`.
5. On relaunch the player sends its new `app_version` on the next ping;
   the displays page's "Reported version" column reflects the rollout
   in real time.

A simple `robocopy` step in the release pipeline handles the upload:
```powershell
robocopy .\dist\ \\signage-server\C$\Apps\AISignXV2\static\updates\win\ `
    "latest.yml" "*.exe" "*.exe.blockmap" /R:1 /W:5
```

---

## 4. Renderer wiring

In the renderer, on the existing `EventSource` (see
`docs/CLIENT_COMMAND_PROTOCOL.md`):

```js
es.addEventListener('command', (ev) => {
    let cmd; try { cmd = JSON.parse(ev.data); } catch { return; }
    switch (cmd.action) {
        case 'reload': location.reload(); break;
        case 'reboot': window.electronAPI.reboot(); break;
        case 'update': window.electronAPI.checkAndApplyUpdate(); break;
    }
});
```

Main process (Windows):

```js
const { app, ipcMain } = require('electron');
const { exec } = require('child_process');
const { autoUpdater } = require('electron-updater');

// Tell the server who we are. This is what the displays page will show
// in its "Reported version" column after each update.
process.env.AISIGNX_APP_VERSION = app.getVersion();

ipcMain.handle('reboot', () => {
    exec('shutdown /r /t 0 /f');
});

ipcMain.handle('checkAndApplyUpdate', async () => {
    autoUpdater.autoDownload = true;
    const r = await autoUpdater.checkForUpdates();
    if (r?.updateInfo && r.updateInfo.version !== app.getVersion()) {
        autoUpdater.on('update-downloaded', () => {
            // Restart now and apply. Run-as-admin is supplied by the
            // NSIS perMachine setting; UAC will appear briefly.
            autoUpdater.quitAndInstall(true, true);
        });
    }
});
```

Pass the running app version through to the page so the player JS
sends it on each ping:

```js
// In a preload script:
const { contextBridge } = require('electron');
contextBridge.exposeInMainWorld('AISIGNX_APP_VERSION',
    process.env.AISIGNX_APP_VERSION);
```

The browser-side player (`static/js/display_player.js`) already reads
`window.AISIGNX_APP_VERSION` and includes it on every ping body, so
this is the entire change required for version visibility.

---

## 5. Code signing notes

- An **EV** certificate is preferred — SmartScreen reputation is
  immediate and the install completes without a "Windows protected your
  PC" dialog.
- A **standard** certificate works but every fresh build must accumulate
  reputation; expect end users to see a SmartScreen warning on the
  first installer until enough downloads have happened.
- Set `verifyUpdateCodeSignature: true` (default) so a tampered
  `latest.yml` or a swapped binary cannot be installed by the updater.

---

## 6. Operational checklist

Before publishing an update:

1. Smoke-test the new installer on a clean Windows VM.
2. Push the update to a single test display via
   `/display/<id>?cmd=update`.
3. Watch the `displays.bulk_command` audit row (`/admin/audit-retention`)
   and the **Reported version** column on `/displays/`.
4. Once the test display reports the new version and is still serving
   playlists, push the bulk **Update** to the rest of the fleet from
   `/displays/` (or use the group-level button on `/groups/`).

After publishing:

- Keep the previous installer around for at least one release in case
  you need to roll a display back manually.
- The audit log retains the rollout trail; the Audit Retention admin
  page lets you inspect or extend retention for the
  `displays.bulk_command` action specifically.
