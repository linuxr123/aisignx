# AISignX Clients

Three native clients are available: **Windows**, **Linux** (Electron), and **Android**.
All use the same registration and player flow ù the server manages everything centrally.

---

## Registration Flow (all clients)

1. Launch the app on the display device
2. Enter the server URL (e.g. `http://192.168.1.10:5000`)
3. Enter a friendly display name
4. The app sends a registration request and shows a waiting screen with a Device ID
5. An administrator opens **Displays** in the server admin and approves the request
6. The app detects approval (polls every 5 seconds), saves the token, and launches the player
7. On every future launch the token is used directly - no setup needed again

Native setup files downloaded from the admin can be imported directly in the
Electron and Android setup screens. Use the **Import setup file** / file picker
button, select the JSON file, then save or register.

To reset: tap/click **Reset Setup** on the waiting screen, or delete `config.json` from the user-data folder.

---

## Building All Clients

The quickest way to build everything is to use the provided scripts from the project root.

### Windows (builds Electron Win+Linux AND Android)
```powershell
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1
```

### Linux / macOS (builds Electron Linux AND Android; Windows .exe requires wine)
```bash
chmod +x build_clients_linux.sh && ./build_clients_linux.sh
```

Both scripts:
- Install npm dependencies
- Build Electron packages for all supported targets
- Build the Android debug APK (no signing required for sideloading)
- Copy all outputs to `static/clients/` so the Downloads page serves them immediately

---

## Electron Client (Windows + Linux)

### What it does
- **Fullscreen kiosk window from launch** ù no title bar, no taskbar, no browser chrome, no visible windowed phase
- **Per-user install** on Windows (no UAC prompt at install or update time)
- **Silent auto-update** ù admin clicks one button and the display updates itself unattended
- Prevents screen sleep via Electron `powerSaveBlocker`
- Checks for updates 30 seconds after launch, then every 60 minutes
- Listens for **server-pushed commands** (Reload / Reboot / Update Client) ù see [Display Push Commands](#display-push-commands)
- Config stored in OS user-data directory (persists across updates)
- File logger at `aisignx-player.log` for diagnosing issues without DevTools
- **Diagnostic hotkeys** built in (see below) ù usable even on production builds

### Install pre-built packages

Download from the server **Downloads** page (`/downloads`).

| Platform | Package |
|---|---|
| Windows 10/11 x64 | `AISignX-Player-Setup.exe` (NSIS one-click, per-user install ù no admin required) |
| Linux x64 | `AISignX-Player.AppImage` (portable, no install needed) |
| Linux Debian/Ubuntu | `AISignX-Player.deb` |

### Build from source manually

Requirements: Node.js 18+, npm

```bash
cd clients/electron-client
npm install
npm run build:win        # Windows installer
npm run build:linux      # Linux AppImage + deb
npm run build:all        # Both
```

Outputs go to `clients/electron-client/dist/`. Copy them to `static/clients/`.

### Per-user install (Windows)

Starting with Electron client v1.0.4+, the Windows installer is configured as **per-user** (`perMachine: false`) so:

- ? **No UAC / admin prompt** at install or update time
- ? Installs to `%LOCALAPPDATA%\Programs\aisignx-player\` (not Program Files)
- ? Auto-update can run completely silently with no user interaction
- ? Each Windows user needs their own install ù fine for kiosks where one account is always logged in

If migrating from an older `perMachine: true` build, first uninstall the old version (Settings ? Apps ? AISignX Player ? Uninstall) before installing the new one. Once on the new build, all subsequent updates are seamless.

### Config file locations

| OS | Path |
|---|---|
| Windows | `%APPDATA%\aisignx-player\config.json` |
| Linux | `~/.config/aisignx-player/config.json` |

The Windows update process explicitly preserves AppData, so registration tokens survive across updates.

### Diagnostic log files (Windows)

| File | Purpose |
|---|---|
| `%APPDATA%\aisignx-player\aisignx-player.log` | Main app log ù startup, commands, update progress |
| `%APPDATA%\aisignx-player\aisignx-update-wrapper.log` | Step-by-step trace of the most recent silent update |
| `%APPDATA%\aisignx-player\aisignx-update-wrapper.stderr.log` | PowerShell errors during update (if any) |

Open them with Notepad or `Get-Content` in PowerShell.

### Diagnostic hotkeys (Electron only)

Production builds hide DevTools and window chrome ù these hotkeys are the only way to escape kiosk or diagnose problems on a deployed display. They register globally on the OS, so they fire even when other windows have focus.

| Hotkey | Action |
|---|---|
| **Ctrl+Alt+L** | Open `aisignx-player.log` in the system text editor |
| **Ctrl+Alt+D** | Open Chromium DevTools on the player window (detached) |
| **Ctrl+Alt+Q** | Force-quit the player (escape kiosk mode) |

### Auto-start on Windows

The NSIS installer adds a Start Menu shortcut. To auto-start on login, add a shortcut to:
```
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
```
Point it at `%LOCALAPPDATA%\Programs\aisignx-player\AISignX Player.exe`.

### Auto-start on Linux

Copy the `.desktop` file from `dist/linux-unpacked/` to `~/.config/autostart/`.

---

## Display Push Commands

Admins can push three one-off commands to a connected display from the **Display detail page**:

| Button | Action | Browser client | Electron client | Android client |
|---|---|---|---|---|
| **Reload** | Reload the player page | Page reload | Window reload | WebView reload |
| **Reboot App** | Restart the player application | Falls back to page reload | App fully relaunches | Activity relaunches |
| **Update Client** | Download + install latest client version | Falls back to page reload | Silent unattended update | APK update; silent only when Device Owner |

### How they work

1. Admin clicks the button ? server queues a command on the display's SSE channel
2. The player page receives the command instantly (typically <100 ms)
3. Browser clients honor `reload` only ù `reboot` and `update` fall back to a page reload
4. Electron clients act on all three via the `signage.runCommand()` IPC bridge
5. Android clients act on all three natively; update uses Android `PackageInstaller`

If the display is offline at click time, the admin sees a popup that says the display is not connected; the command is discarded (not queued ù they're one-shot, no replay).

### Update Client flow (Electron / Windows)

The full silent update process:

1. Player downloads the latest installer from `/static/clients/AISignX-Player-Setup.exe`
2. Player writes a PowerShell wrapper script to `%TEMP%`
3. Player registers a one-shot Windows Scheduled Task to run the wrapper ~75 seconds in the future
4. Player exits cleanly (releases all file locks)
5. Task fires ? PowerShell silently runs the installer (NSIS `/S` flag)
6. Installer replaces the app files and exits
7. PowerShell launches the new exe maximized
8. New player comes up in fullscreen kiosk mode
9. Wrapper deletes the scheduled task and the temporary script

**Total downtime:** ~80 seconds of black screen on the display, fully unattended.

The wrapper writes detailed logs to `aisignx-update-wrapper.log` in case anything fails.

### Why a Scheduled Task?

Earlier approaches (spawning the installer directly, batch wrappers, detached PowerShell) were unreliable: when the parent app exits, Windows can kill the orphan child despite `detached: true`. Scheduled Task is a Windows service running independently of any process tree, so it survives our app exit guaranteed. This is the same pattern Chrome's auto-updater uses.

---

## Android Client

### What it does
- Native Kotlin app with hardware-accelerated WebView player
- Fullscreen immersive - all system UI hidden
- Back and Menu keys blocked in kiosk mode
- Screen kept on at all times (KEEP_SCREEN_ON flag)
- Auto-starts after device reboot via BootReceiver
- Imports downloaded setup JSON files from the setup screen
- Listens for server-pushed Reload / Reboot App / Update Client commands
- Checks for APK updates automatically. If the display has **Auto-update client**
  enabled, it checks immediately after the player page reports that setting and
  then every 15 minutes.
- Supports fully unattended APK updates when provisioned as Android Device Owner
- Long-press on the WebView opens the in-player unlock PIN keypad

### Install pre-built APK
Download `AISignX-Player.apk` from the server Downloads page.

1. On the Android device, go to **Settings -> Apps -> Special app access -> Install unknown apps**
2. Allow your browser or file manager to install APKs
3. Transfer the APK and tap to install
4. Or via ADB: `adb install AISignX-Player.apk`

### Build from source manually

Requirements: Android Studio Hedgehog (2023.1.1+), Android SDK 34, Java 17

```bash
cd clients/android-client
./gradlew assembleDebug
# APK: app/build/outputs/apk/debug/app-debug.apk
```

For a signed release build used by the Downloads page:

```bash
cd clients/android-client
./gradlew assembleRelease
# APK: app/build/outputs/apk/release/app-release.apk
```

Copy the release APK to `static/clients/AISignX-Player.apk` and bump
`clients.android.version` in `static/clients/client_versions.json`.

### Kiosk / Dedicated device setup
For a fully locked-down kiosk, set the app as the device launcher:
- **Settings -> Apps -> Default apps -> Home app -> AISignX Player**

This prevents users from ever leaving the app.

### Silent Android auto-update

Android only permits no-touch APK installs for privileged or managed apps. For
normal sideloaded installs, Android may still require the system **Install**
approval screen even when AISignX downloads the update successfully.

For unattended kiosk updates, provision AISignX Player as **Device Owner** on a
fresh/factory-reset device:

```powershell
adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver
```

Requirements:
- The device must normally be factory reset first; Android rejects Device Owner
  setup if accounts or another owner are already configured.
- The installed APK must include `AisignxDeviceAdminReceiver` (Android client
  `1.4.6+`).
- Future APKs must use the same signing key as the installed app.
- The server manifest version must be strictly higher than the installed
  `versionName`.
- `static/clients/AISignX-Player.apk` must exist and match the manifest URL.

Once Device Owner is set and the display has **Auto-update client** enabled,
the Android client can download, install, and relaunch without local user
interaction.

---

## Update System

There are now two complementary update paths:

### A) Background check (all native clients)

1. The server hosts a version manifest at `GET /api/version` (loaded from `static/clients/client_versions.json`)
2. Electron checks shortly after launch and periodically after that.
3. Android checks shortly after launch when local update mode is prompt/auto.
   If the server-side display setting **Auto-update client** is enabled, Android
   starts an immediate check and repeats every 15 minutes.
4. If the remote version is higher than the installed version:
   - **Prompt mode** shows a modal/dialog asking the operator to update
   - **Auto mode** downloads and installs automatically where the OS permits it

This path is appropriate for client roll-outs that should happen without an
operator opening the admin page. For Android, fully unattended install requires
Device Owner as described above.

### B) Admin-pushed update

Use the **Update Client** button on the Display detail page (see [Display Push Commands](#display-push-commands)). This is best for production kiosks and off-hours maintenance.

Electron Windows updates are unattended using the scheduled-task wrapper.
Android updates are unattended only when Android grants silent install approval
(Device Owner / managed kiosk); otherwise Android shows its system install
approval.

### Publishing a new version

The root build scripts support **selective targets** and **optional version bump**:

| Flag (PowerShell) | Flag (bash) | Effect |
|-------------------|-------------|--------|
| `-Electron` | `--electron` | Build/copy Electron only |
| `-Android` | `--android` | Build/copy Android only |
| (none) | (none) | Both targets |
| `-NoBump` | `--no-bump` | Build without incrementing source versions |
| `-BumpOnly` | `--bump-only` | Bump + update manifest only (no compile) |
| `-Help` | `--help` | Show usage |

When bump is enabled (default), patch +1 is applied to:

| File | Change |
|------|--------|
| `clients/electron-client/package.json` | `version` patch +1 |
| `clients/android-client/app/build.gradle.kts` | `versionCode` +1, `versionName` patch +1 |
| `server/static/clients/client_versions.json` | Updated for selected targets only |

Examples (repo root):

```powershell
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Android
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -Electron -NoBump
powershell -ExecutionPolicy Bypass -File build_clients_windows.ps1 -BumpOnly
```

```bash
./build_clients_linux.sh
./build_clients_linux.sh --android
./build_clients_linux.sh --electron --no-bump
./build_clients_linux.sh --bump-only
```

Manual path: bump `package.json` / `build.gradle.kts` yourself, build, copy into `server/static/clients/`, then use the Downloads **Manifest Editor** if you did not use the script.

> **Important:** The Manifest Editor on the Downloads page writes UTF-8 without a BOM. If you edit `client_versions.json` directly with PowerShell `Set-Content` or Notepad, Windows may add a BOM that older clients cannot parse. The current server is BOM-tolerant (uses `utf-8-sig`), but the cleanest path is always the Manifest Editor.

After saving the manifest, all running clients pick up the new version on their
next update check, OR the admin can immediately push it to specific displays via
**Update Client**.

### Version format

Standard semver: `"1.2.3"`. Clients compare each part numerically. Updates are only offered when the remote version is **strictly greater** than the installed version.

---

## Troubleshooting client problems

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for installer / update / connection issues.

Quick reference for an Electron client that won't launch or won't update:

1. **Ctrl+Alt+Q** ù force-quit the player (escape kiosk if it's hung)
2. **Win+R** ? `%LOCALAPPDATA%\Programs\aisignx-player\AISignX Player.exe` ù manual launch to test the binary
3. **Win+R** ? `%APPDATA%\aisignx-player\` ù open the log folder
4. Open `aisignx-player.log` in Notepad ù check the most recent entries for errors
5. If an update failed, also open `aisignx-update-wrapper.log` ù it has timestamps for every step of the install
