# AiSignX Player (Electron)

A kiosk shell around the AiSignX v3 browser player for Windows (and Linux, untested).
It does not re-implement playback: Chromium shows the server's `/player/` page, loaded
from a small caching proxy inside the app.

```
Chromium  ->  http://127.0.0.1:<port>/player/  ->  loopback proxy (main process)  ->  AiSignX server
                                                    |- page/JS snapshot (offline cold start)
                                                    |- media cache (offline playback)
                                                    '- TLS trust and certificate pinning
```

- **Pairing** is the browser player's own flow: the screen shows a code, an admin enters it
  at `<server>/displays/pair`. The credential is an HttpOnly cookie kept in Chromium's
  persistent profile. No password, token or code is ever typed on the device.
- **Offline:** when the server goes away the player keeps playing. A restart while the
  server is down plays from the page snapshot and the media cache. A screen that was never
  online shows an offline page that retries every 10 s.
- **Web items** (iframes) go straight to their sites with normal TLS checks.

## Requirements

- Node.js 22.15 or newer (24 recommended) and npm.
- An AiSignX v3 server reachable from the screen.

## Develop

```powershell
cd clients\electron-client
npm ci            # exact versions from package-lock.json
npm test          # unit tests, plain Node (no Electron needed)
npm start         # opens setup on first run
```

If `npm start` says Electron failed to install, run `node node_modules\electron\install.js`
once (it downloads the Electron binary).

### Smoke test

With a server running on `http://127.0.0.1:5000`:

```powershell
npm run smoke
# or against another server:
npx electron . --smoke --server=http://127.0.0.1:5873
```

Smoke mode uses a temporary profile, skips setup, kiosk and global shortcuts, and starts
the proxy on a free port. It waits until the player shows its pairing or setup-wait screen,
checks that `window.AiSignXShell` exists, that `/__shell/offline` answers 503 and that
`/healthz` passes through, then prints `SMOKE OK ...` and exits 0 (or exits 1 after 30 s).
`--port=N` keeps the same loopback origin across runs.

## Build the installer

```powershell
npm run dist:win     # dist-build\AiSignX Player Setup 3.0.0.exe
npm run dist:linux   # AppImage and deb (run on Linux; not verified yet)
```

`dist:win` first runs `scripts\prebuild_clean.ps1`, which stops a running player and
clears `dist-build\`. If electron-builder fails to extract `winCodeSign` with a symlink
error, enable Windows Developer Mode. The installer is not code-signed, so expect a
SmartScreen warning.

The app ID is `com.aisignx.player`, the same as v2, so installing v3 replaces the v2
app. v3 keeps its data in its own folder, so nothing from v2 is reused or overwritten.

## Setup on the screen

1. Enter the server address, for example `192.168.1.20:5000` or `https://signage.example.lan`,
   and press **Test**. The test calls `/healthz` and must find an AiSignX server.
2. HTTPS with a certificate trusted by Windows (a public CA or your own CA installed in the
   Windows certificate store) just works.
3. A self-signed certificate shows a trust card with its SHA-256 fingerprint. Compare it with
   `openssl x509 -noout -fingerprint -sha256 -in cert.pem` on the server, then tick
   **Trust this certificate**. Only that exact certificate is accepted afterwards (pinning).
   If the server's certificate changes, the screen shows "Server certificate changed" and
   keeps playing cached content until you re-run setup. A certificate issued by a private CA
   cannot be pinned: install that CA in the Windows store instead.
4. Plain http to a non-private address shows a warning but is allowed.
5. **Save and start** opens the player full screen; pair it in the admin.

Changing the server, or **Forget server**, clears the pairing cookie, local storage, both
caches and the pin. The client ID stays. Changing only the server's IP address does not
force a re-pair, but the new address must be entered in setup.

## Keys

| Keys | Action |
|---|---|
| Ctrl+Alt+S | Open setup |
| Ctrl+Alt+L | Open the log file |
| Ctrl+Alt+D | Open DevTools (detached) |
| Ctrl+Alt+Q | Quit |

Reload, DevTools, close, zoom and back-navigation keys are blocked in the player.

When the server sets an unlock PIN for the display, every shortcut above asks for it first.

## Player controls

With **Player controls** on in the display's settings, the app forwards Space/Enter
(pause/resume), the arrow keys (seek 10 s or change item), PageUp/PageDown and the media keys
to the player page; touch, mouse and wheel work on the page itself. See
`docs/player-api.md` ("Player controls") for the full mapping. With the option off, these keys
are ignored as before.

Media keys: the app starts Chromium with `--disable-features=HardwareMediaKeyHandling`, so
Play/Pause, Next and Previous arrive as normal key presses instead of going to the Windows
media overlay. They work while the player window has focus.

## Lock player

With **Lock player** on (it needs an unlock PIN on the server), the player window cannot be
closed (Alt+F4, the taskbar) or minimized, stays on top of other windows and takes the focus
back when something else gets it. Setup, the PIN prompt and DevTools still come to the front
while they are open. Ctrl+Alt+Q still quits after the PIN, and Windows logoff and shutdown
are never blocked.

An admin can press **Unlock remotely** on the display page: for the chosen time the lock is
lifted and the shortcuts run without the PIN. **Lock again** ends that early; a restart always
locks again. If a locked display somehow has no valid PIN hash, the shortcuts run without a
PIN, so a screen can never be locked for good.

An app cannot block Alt+Tab, the Windows key or Ctrl+Alt+Del; it only takes the focus back.
For a fully locked PC (auto-logon, Shell Launcher or Assigned Access, policies) see
`docs/kiosk-lockdown.md`.

## Device agent

Off by default; the admin turns each part on per display (Displays > display > Edit > Device agent).

- **Watchdog.** A current-user Scheduled Task (`AiSignXWatchdog`, every minute, no administrator
  rights, no service) runs `watchdog.ps1`. It restarts the player when it is not running or when
  its heartbeat file is older than 90 s (a hung player is killed first). At most 6 restarts per
  hour; then it reports `gave_up` and stops until the player is seen healthy again. The app writes
  the heartbeat only while the page proves it is alive (the page sends `alive` every 10 s; the
  offline page counts too). The app creates the task when the switch is on and removes it when it
  is off, or when the server is forgotten in setup. Quitting on purpose (PIN exit, setup Quit)
  writes a "stopped by user" marker so the watchdog does not bring the player back; the next start
  clears it.
- **Restart player** and **Collect logs.** The app polls its own commands every 20 s over its pinned
  connection (never through the page). Only these two verbs exist; each runs at most once; a verb
  whose switch is off is refused. The log (with the previous generation) is redacted (tokens,
  cookies, unlock PIN hash, certificate pin) and uploaded gzipped, at most 900 KB (the newest part).
- Files: `%LOCALAPPDATA%\AiSignX\agent\` (`flag.json`, `heartbeat.json`, `state.json`, `stopped`,
  `watchdog.ps1`, `watchdog.log`) and `agent-handled.json` in the data folder. The installer ships the
  script as `resources\watchdog.ps1`. Uninstalling does not remove the task; it does nothing once the
  exe is gone (`schtasks /Delete /TN AiSignXWatchdog /F` removes it).

Honest limits:

- A scheduled task restarts the player within about a minute, not instantly.
- It protects against crashes and hangs, not against a local user who kills the task, edits it or
  deletes the agent folder.
- No reboot and no updates (not included in this release).
- After a Windows restart the player only returns if Windows logs the user in again (auto-logon,
  see the checklist below). The task runs only while that user is logged on.
- Only an installed app registers the task, not `npm start`.

Manual test checklist (a real PC; nothing here has run on hardware yet):

1. Install, pair, enable Watchdog, Restart and Logs on the display. Within a minute `schtasks /Query /TN AiSignXWatchdog` shows the task, and the display page shows the agent capabilities.
2. End the player in Task Manager: it is back within about 1-2 minutes, no console window flashes, and the display shows 1 restart.
3. Suspend the main process (Process Explorer): after about 90 s the watchdog kills and restarts it.
4. Quit with Ctrl+Alt+Q and the PIN: it stays closed. Start it by hand: the watchdog resumes.
5. Kill it 7 times within an hour: the 7th time it is not restarted and the display shows "gave up".
6. Turn Watchdog off in the admin: the task disappears; killing the player does nothing.
7. Press Restart player: the screen restarts within seconds, the command shows done once; a second press works again.
8. Press Collect logs: a download appears on the display page; check it holds no token, cookie, PIN hash or certificate pin.
9. With Restart off, a restart command is refused ("switched off"). Pull the network during a log upload: the command ends failed, never twice.
10. Stop the server for a few minutes: the player shows the offline page and is not restarted by the watchdog.
11. Reboot with auto-logon: the player comes back (login item) and the task keeps running.

## Kiosk PC checklist (Windows)

- A dedicated Windows user with automatic logon.
- Power plan: display and sleep set to Never.
- Notifications and Windows Update restart prompts off (active hours, or managed updates).
- The installed app starts at logon (setup checkbox; it uses a Windows login item).
- Only one instance runs; a second start focuses the first.
- The display is kept awake while the player runs. A crashed renderer is reloaded after 2 s
  (more than 5 crashes in 10 minutes restarts the app), and a hung one is reloaded.

## Files and data

Data lives in `%APPDATA%\AiSignX Player\v3` (`~/.config/AiSignX Player/v3` on Linux):

| Path | Content |
|---|---|
| `config.json` | `serverUrl`, `port`, `certPin` (`sha256`, `caPem`), `autostart`, and from the server `unlock` (PIN hash), `allowInput`, `kioskLock` |
| `client_id.txt` | 32 hex characters, created once |
| `aisignx-player.log` | Log; at 2 MB it moves to `aisignx-player.log.1` (one older file is kept). Never contains cookies or request bodies. |
| `page-cache\` | Snapshot of `/player/` and its scripts |
| `media\` | Cached media and `index.json`; LRU, at most 10 GB or half the free disk space |

The loopback port (47800-47899) is chosen once at the first save and never changes on its
own, because the pairing cookie and the player's local storage belong to that origin. If
another program holds the port at startup, the screen says "Local port in use" and retries.

## Security notes

- The proxy listens on 127.0.0.1 only and answers 421 to any other `Host` header (DNS
  rebinding). Cached media is served only to requests that carry the screen's own cookie.
- Chromium never talks to the AiSignX server directly, so there is no certificate-error
  override anywhere. TLS to the server is verified in Node with the bundled and Windows CA
  stores plus, optionally, the pinned certificate.
- The proxy removes the `Secure` attribute from cookies, because the hop to Chromium is
  `http://127.0.0.1`. That hop never leaves the machine.
- Renderers are sandboxed with context isolation. The player page gets only
  `window.AiSignXShell` (device info, state and manifest notices); web-item iframes get nothing.
- Camera, microphone, location and similar permissions are always denied.

## Layout

```
src/main.js            app lifecycle, IPC, wiring
src/config.js          config.json, client_id, server URL rules
src/trust.js           CA list, pinning agent, certificate capture
src/health.js          setup's /healthz test
src/agent.js           device agent: commands, heartbeat file, watchdog task
src/agent/             logic (pure rules), task (schtasks)
resources/watchdog.ps1 the watchdog script (shipped with the installer)
src/proxy/             loopback proxy: server, upstream, page-cache, media-cache, range
src/window.js          player window and watchdogs
src/kiosk.js           blocked keys, control keys, shortcuts, keep-awake, autostart
src/lock.js            Lock player state and remote unlock
src/unlock-window.js   PIN prompt in front of the shortcuts
src/setup.js           setup window and its IPC
src/preload-*.js       sandboxed preloads
src/ui/                setup and offline pages
test/                  node:test unit tests
```

## Deferred

Auto-update, remote reboot, macOS builds and code signing come later.
