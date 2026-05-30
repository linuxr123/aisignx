# AISignX Player - Electron Client

Cross-platform display client for Windows and Linux. Wraps the AISignX web player in a fullscreen kiosk window with native setup screens, server-approval registration, and silent admin-pushed updates.

## Requirements
- Node.js 18+
- npm

## Development

```
cd clients/electron-client
npm install
npm start
```

## Building packages

```
npm run build:win    # Windows installer (.exe / NSIS, per-user, no UAC)
npm run build:linux  # Linux AppImage + .deb
npm run build:all    # Both
```

Outputs go to `clients/electron-client/dist/`. Copy the relevant installer into `server/static/clients/` and bump `client_versions.json` to publish (or run `build_clients_*.ps1` / `.sh` from the repo root).

## Releasing a new version

1. Bump `version` in `package.json` (e.g. `1.0.10` -> `1.0.11`)
2. `npm run build:win`
3. Copy `dist/AISignX Player Setup <version>.exe` to `<server>/static/clients/AISignX-Player-Setup.exe`
4. In the admin **Downloads** page, open the **Manifest Editor** and bump `windows.version` to match
5. Either wait up to 60 minutes for the hourly check, or push immediately via **Update Client** on the Display detail page

## First-run flow

1. App launches in fullscreen kiosk -> shows **Setup** screen
2. Enter the server URL (e.g. `https://aisignx.example.com`)
3. Enter a friendly name for this display
4. App sends a registration request to the server and shows **Waiting for Approval**
5. Administrator opens **Displays** in the server admin and clicks **Approve**
6. App detects approval, saves the API token locally, and loads the player immediately

The window starts in fullscreen kiosk from the first paint - there is no visible "windowed then fullscreen" transition.

## PIN unlock & desktop access

When the display has an unlock PIN configured (server-side, per display), a 1.5s long-press (or Enter on a remote) pops the on-screen keypad. Entering the correct PIN **minimizes the kiosk** so a technician can use the desktop underneath. The kiosk stays minimized until either:

- the operating system has been idle for 5 minutes (`powerMonitor.getSystemIdleTime`), or
- the user brings the window back (taskbar / Alt-Tab),

at which point the player re-asserts fullscreen kiosk and re-locks automatically.

## Offline playback

The player caches its page, plugins, and media (images/videos) in a service worker so content keeps playing when the server is unreachable. Chromium only enables service workers in a secure context, so on plain-HTTP LAN deployments the client automatically marks the configured server origin as a trusted secure origin (`--unsafely-treat-insecure-origin-as-secure`). HTTPS deployments work without this. Videos that stall mid-stream are skipped after a few seconds instead of freezing the playlist.

The server URL and token are stored in the OS user-data directory:
- Windows: `%APPDATA%\aisignx-player\`
- Linux: `~/.config/aisignx-player/`

## Diagnostic hotkeys

Global hotkeys that work even on production builds (where the DevTools menu is hidden):

| Hotkey | Action |
|---|---|
| **Ctrl+Alt+L** | Open the player log file in Notepad / xdg-open |
| **Ctrl+Alt+D** | Open Chromium DevTools on the player window |
| **Ctrl+Alt+Q** | Force-quit the player (escape kiosk) |

## Log files (Windows)

| File | Purpose |
|---|---|
| `%APPDATA%\aisignx-player\aisignx-player.log` | Main app log (startup, commands, update progress) |
| `%APPDATA%\aisignx-player\aisignx-update-wrapper.log` | Step-by-step trace of the most recent silent update |
| `%APPDATA%\aisignx-player\aisignx-update-wrapper.stderr.log` | PowerShell errors during update (if any) |

## Server-pushed commands

The player listens for SSE `command` events from the server. Three actions are supported:

| Action | Effect |
|---|---|
| `reload` | Reload the kiosk window |
| `reboot` | `app.relaunch()` + `app.exit()` - full app restart |
| `update` | Download the latest installer, schedule a silent install via Windows Task Scheduler, and quit. New version auto-launches in fullscreen ~80s later |

Admins trigger these via the **Reload / Reboot App / Update Client** buttons on each Display detail page. See [../docs/CLIENTS.md](../docs/CLIENTS.md) for the full flow.

## Per-user vs. per-machine install

This client builds as a per-user NSIS installer (`perMachine: false`):

- No UAC prompt at install or update time
- Installs to `%LOCALAPPDATA%\Programs\aisignx-player\` (not Program Files)
- Auto-update can run completely silently
- Each Windows user account needs its own install - fine for kiosks where one account is always logged in

## Resetting

Click **Reset Setup** on the waiting screen, or delete `config.json` from the user-data path, to start the setup flow again. Or use **Ctrl+Alt+Q** to force-quit and restart manually.

## Autostart

**Windows:** the NSIS installer adds a Start Menu shortcut. To auto-start on login, copy the shortcut to `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`.

**Linux:** copy the `.desktop` file from `dist/linux-unpacked/` to `~/.config/autostart/`.
