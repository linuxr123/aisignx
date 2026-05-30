# Display Command Protocol

The server can push these actions to a connected display via the
SSE `command` event. Both the per-display button on `/displays/<id>` /
`/displays/`, the bulk toolbar on `/displays/`, and the group-level
button on `/groups/` all funnel through this same protocol.

```
event: command
data:  {"action": "reload"|"reboot"|"update"|"release_device_owner", ...optional payload}
```

## Action contracts

| Action  | Required behavior                                                                                                   | Notes |
|---------|---------------------------------------------------------------------------------------------------------------------|-------|
| `reload`| Re-fetch the player URL (or hard-reload the page).                                                                  | Always supported. Browser kiosks just call `location.reload(true)`. |
| `reboot`| Restart the client application/activity.                                                                            | Electron and Android native shells. Browser kiosks fall back to reload or ignore. |
| `update`| Trigger the client's self-update routine, then restart the player.                                                  | Electron and Android native shells. Android silent install requires Device Owner. |
| `release_device_owner`| Android: relinquish Device Owner (`DevicePolicyManager.clearDeviceOwnerApp`) and drop screen pinning, so the kiosk can be uninstalled or re-provisioned without a factory reset. | Android only; other clients ignore it. Silent auto-update stops working until the device is re-provisioned as Device Owner. |

The server records the push as the `display.command`, `group.command`,
or `displays.bulk_command` audit action together with the delivered
flag, so the operator can see in `/admin/audit-retention` whether a
display was actually online at push time.

## Reference Electron handler

The Electron player should listen for `command` events on its existing
SSE connection. A minimal handler:

```js
// Inside the renderer (or main, via IPC):
const es = new EventSource(`/display/${token}/events`);

es.addEventListener('command', (ev) => {
    let cmd; try { cmd = JSON.parse(ev.data); } catch { return; }
    switch (cmd.action) {
        case 'reload':
            location.reload();
            break;
        case 'reboot':
            // Renderer asks main to reboot the OS.
            window.electronAPI.reboot();
            break;
        case 'update':
            // Run the auto-updater (electron-updater / Squirrel).
            window.electronAPI.checkAndApplyUpdate();
            break;
    }
});
```

Suggested main-process bindings (Windows):

```js
const { ipcMain } = require('electron');
const { exec } = require('child_process');
const { autoUpdater } = require('electron-updater');

ipcMain.handle('reboot', () => {
    // /t 0 = no countdown; /f = force-close apps; /r = reboot
    exec('shutdown /r /t 0 /f');
});

ipcMain.handle('checkAndApplyUpdate', async () => {
    const r = await autoUpdater.checkForUpdates();
    if (r?.updateInfo) {
        await autoUpdater.downloadUpdate();
        autoUpdater.quitAndInstall(true, true);   // restart after install
    }
});
```

## Android handler

The Android client listens to the same SSE `command` event natively:

- `reload` calls `WebView.reload()`
- `reboot` restarts `PlayerActivity`
- `update` checks `/api/version`, downloads `clients.android.url`, and commits
  an Android `PackageInstaller` session

Android unattended install is controlled by the operating system. Provision the
device as Device Owner for fully silent updates:

```powershell
adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver
```

On non-Device-Owner devices, Android may show the standard install approval
screen even when the server display setting **Auto-update client** is enabled.

## Browser-only kiosks

Pure browser kiosks (Edge, Chrome, Android Chrome without the AISignX native app)
cannot reboot or update the host application. They:

- **Must** handle `reload`.
- **Should** ignore `reboot` and `update` silently (no error toasts).

The server reports `delivered: true` when the SSE event made it onto
the wire; whether the client acted on it is only observable through
behavior (e.g. the next `/ping` showing a fresh `version`).
