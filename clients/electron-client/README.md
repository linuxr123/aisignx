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
| `config.json` | `serverUrl`, `port`, `certPin` (`sha256`, `caPem`), `autostart` |
| `client_id.txt` | 32 hex characters, created once |
| `aisignx-player.log` | Log, truncated at 2 MB. Never contains cookies or request bodies. |
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
src/proxy/             loopback proxy: server, upstream, page-cache, media-cache, range
src/window.js          player window and watchdogs
src/kiosk.js           blocked keys, shortcuts, keep-awake, autostart
src/setup.js           setup window and its IPC
src/preload-*.js       sandboxed preloads
src/ui/                setup and offline pages
test/                  node:test unit tests
```

## Deferred

Auto-update, PIN unlock, remote restart, macOS builds and code signing come later.
