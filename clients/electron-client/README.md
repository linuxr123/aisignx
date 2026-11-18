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
- Reboot, startup changes and updates are a separate, signed kind of command: see the next section.
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

## Privileged agent (Windows, optional)

For PCs that should reboot, change startup, update the app and sign in automatically on command from the server
without anyone logged in as administrator. A small separate program runs as SYSTEM (a scheduled task, started at boot
and restarted on failure) using this app's own Electron binary in node mode. It obeys only commands signed by the
server's pinned key and installs only installers signed with the pinned certificate. Everything is in
[../../docs/windows-agent.md](../../docs/windows-agent.md); the protocol part is in
[../../docs/signed-commands.md](../../docs/signed-commands.md).

Build (on the PC that holds the signing certificate):

```powershell
$env:AISIGNX_SIGN_THUMBPRINT = '<40 hex of your code-signing certificate>'   # never put it in a tracked file
npm run dist:win              # installer; the hook signs the app, uninstaller and installer
npm run agent:win             # dist-buildgent\AiSignX-Agent-<version>.zip and a signed Install-Agent.ps1
```

Install on the screen PC, as administrator, with the command line the display page shows
(Display > Privileged agent > Enroll the privileged agent). `.\Install-Agent.ps1 -DryRun ...` checks every parameter
and changes nothing. `.\Install-Agent.ps1 -Uninstall` removes it. While the agent is installed and running, the
player's own reboot/startup/update code stands down; without it that code works as before.

## Remote reboot, startup and updates

Three more commands, **off by default** (switches `reboot`, `startup`, `update` per display, Edit > Device agent).
They can take a screen out of service or run new code, so each one is **signed by the server's command key** and
the app obeys only the signed copy. Protocol: [docs/signed-commands.md](../../docs/signed-commands.md).

- **Trust on first use.** The app fetches the server's public key (`/command-key`) and pins the first one it sees
  for that server, in `agent-state.json` in the data folder. A different key later is refused, and the display's
  diagnostics show `command_key_changed`. Compare the fingerprint on the server (Admin > System) with the device
  when you pair it. Forgetting the server or changing the server address clears the pin.
- **Every command is checked:** signature, version, this display's id, request id and action, expiry (against the
  server's clock, not the PC's), a sequence number higher than any accepted before (saved before acting, so a
  command runs once even if the PC crashes), typed parameters, and the local switch. A failure is acknowledged
  `failed` with the reason and nothing runs.
- **Reboot.** The app runs `shutdown.exe /r /t 30` (30 s warning, fixed text) and acknowledges "rebooting" only when
  it accepted the request (an already pending shutdown is reported as failed); the 30 s delay lets the acknowledgement
  out. At most once per 10 minutes, counted only when the restart was really scheduled. After the reboot the player only returns if Windows signs the user in (auto-login below).
- **Startup.** `autostart` turns the per-user start at sign-in on or off (the same login item as the setup checkbox).
  `mode: shell` makes the player the Windows shell **for the signed-in user only** (`HKCU\...\Winlogon\Shell`);
  `mode: app` removes it again. The app removes only a shell value it wrote itself. Both succeed only when reading the
  registry back shows the new state; a launcher path containing a double quote is refused.
- **Update.** The app downloads the installer from the server (with the display's credential) into a fresh random
  folder under `updates\` in the data folder (exclusive open, 15-minute overall deadline), hashing while it streams,
  and keeps it only when the size and SHA-256 equal the **signed** values. It acknowledges "installing <version>",
  hashes the file again right before it runs, starts the installer silently (`/S`) as a detached program, and quits
  only after the installer really started (a start failure keeps the player running and shows in the diagnostics as
  `last_update_error`). The quit writes an `updating` marker instead of `stopped`, so the shell launcher and the
  watchdog wait up to 10 minutes for the new version rather than starting Explorer. At most one update per
  10 minutes, counted when the installer starts. Leftover installers are deleted at the next start.
- **Replay and clock.** The app stores the highest `seq` accepted and the highest `server_time_ms` seen per server;
  a response whose server time is lower is refused (`bad_time`), so an old signed command cannot be revived by
  rewinding the unsigned clock. "Forget server" clears the pin, `seq` and the time mark together. The pinned key's
  fingerprint is in the diagnostics (`command_key_fingerprint`); compare it with Admin > System.
- Nothing the server sends is ever put into a command line. Every program and argument is fixed (`shutdown.exe`,
  `powershell.exe` with constant scripts, the installer with `/S`); parameters are booleans, an enum, a version
  string, a hash and a size.
- Only an installed app on Windows reports `can_reboot`, `can_set_startup` and `can_update`;
  `startup.shell_capable` also needs `shell-launcher.ps1` next to the app.

### Make a locked-down kiosk PC or tablet-style device

1. Use a dedicated, low-privilege Windows user (for example `kiosk`). Sign in once so its profile exists.
2. Install AiSignX Player as that user and pair it (admin enters the code at `<server>/displays/pair`).
3. In the web admin, edit the display: turn on **Reboot**, **Startup** and **Update** (and Restart, Logs, Watchdog).
4. **Auto-login** (local only, below). Then in the web admin set the startup to **start at sign-in**, and for a
   true kiosk **Player as the Windows shell**. From the next sign-in the user sees only the player.
5. Power plan: display and sleep Never. Windows Update: active hours or managed restarts.

### Shell mode: fallback and undo

In shell mode Windows starts `shell-launcher.ps1` instead of Explorer. It keeps the player running and **starts
Explorer instead** (and stays alive while Explorer runs, tolerating a restart of about a minute) when the player was quit on purpose (PIN exit, Ctrl+Alt+Q), when the player exits within 30 s
three times in a row (crash loop), when the player is not installed any more, or when the launcher itself fails.
So the user is not left with a blank desktop. Details are in `%LOCALAPPDATA%\AiSignX\agent\shell-launcher.log`.

Undo, any of:

- web admin: set the startup mode to **Normal app** (signed command; needs the display online and the Startup switch);
- on the device, elevated: `setup-kiosk.ps1 -Do Shell -Undo`;
- last resort, from another administrator account or recovery: delete the value `Shell` under
  `HKEY_USERS\<user's SID>\Software\Microsoft\Windows NT\CurrentVersion\Winlogon` (or load `NTUSER.DAT` of the user).

### Auto-login is local only (`resources\setup-kiosk.ps1`)

Auto-login needs **administrator rights** (it writes under `HKLM`) and the user's **password**. The app runs as a
normal user and must stay that way, and a password must not travel through the server, so this step is a helper you
run on the device in an **elevated** PowerShell (it ships as `setup-kiosk.ps1` in the app's `resources` folder):

```powershell
powershell -ExecutionPolicy Bypass -File "$env:LOCALAPPDATA\Programs\AiSignX Player\resources\setup-kiosk.ps1" -Do AutoLogin
# also set the shell for that user from the device:     -Do Both
# undo (turns auto-login off and deletes the password):  -Do AutoLogin -Undo
```

It asks for the user and the password (typed locally, not shown, never in the command line or a log) and says plainly:
**Windows stores the password in the registry in plain text** (`Winlogon\DefaultPassword`). Use a dedicated account
with no other use and a password used nowhere else. For better protection use Sysinternals Autologon (stores it as an
LSA secret). `-Do Shell` writes the same shell value as the web admin, for any chosen user.

### Honest limits (nothing here has run on real hardware yet)

- The installer is **not code-signed**: Windows SmartScreen may warn on a manual install. A silent install started by
  the app is not interactive, but a managed PC with an application-control policy can still block it.
- A silent update closes the old app and starts the installer. In shell mode the launcher waits up to 10 minutes for the
  new version (it does not start Explorer). The NSIS flags (`/S --updated --force-run`) have not been tested against
  a real installer run. If the installer never relaunches the app, the launcher gives up after 10 minutes and starts
  the player again, then Explorer if that fails too.
- Shell mode needs `powershell.exe` to be allowed (AppLocker or constrained policies can block it; setting the shell
  checks that PowerShell runs, but the launcher itself is only tested by hand). If the screen ever stays black: press
  Ctrl+Alt+Del, Task Manager, Run new task, `explorer.exe`, then undo the shell (see above).
- The installer download is hashed again just before it runs, but another process of the same Windows user could
  still swap the file in that instant; do not share the kiosk account.
- Shell mode is **per user**. It does not stop a person with a keyboard who can reach a key combination Windows
  keeps (Ctrl+Alt+Del and its screen, for example); this is a kiosk helper, not a security boundary.
- **First contact is trust on first use.** The command key is fetched from the server the first time the player
  talks to it and pinned from then on. Someone who can intercept that first exchange (plain http on a network you do
  not control) can pin their own key and later send signed reboot, startup and update commands. Use **HTTPS** with a
  certificate Windows trusts, and compare `command_key_fingerprint` in the diagnostics with Admin > System after
  pairing. (The Android QR code carries the key; Windows has no QR step, so it cannot.)
  Someone with full control of the server holds the key and can sign any command (stated limit of the protocol).
- A Galaxy Tab (Android) is a separate client with its own mechanisms; this section is Windows only.

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
src/agent-svc/         the privileged agent (SYSTEM): main, core, updater, ops, ecies, authenticode, config, state
scripts/               Build-Agent, Sign-File, Sign-Installer, sign-hook.js
resources/Install-Agent.ps1  elevated installer of the agent; resources/agent/ps/ its helper scripts
src/agent/             logic (pure rules), task (schtasks), verify (signed commands), store (key pin, seq),
                       system (shutdown, shell registry), package (update download), tierb (Tier B commands)
resources/shell-launcher.ps1  shell mode launcher with Explorer fallback
resources/setup-kiosk.ps1     elevated local helper: auto-login, shell for a user
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

Auto-update of the player without the agent, and macOS builds (dropped), are not planned.
