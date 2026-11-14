# Player apps (Electron and Android)

AiSignX has two player apps for screens that should run unattended:

- **AiSignX Player for Windows** (Electron, `clients/electron-client`). Linux builds exist but
  are not tested yet.
- **AiSignX Player for Android** (`clients/android-client`): phones, tablets and Android TV
  boxes, Android 8.0 or newer.

Both apps are kiosk shells around the same browser player that runs at `/player/`. Pairing,
playback, schedules, emergency messages and live updates all happen in that page, so a screen
behaves the same in a browser and in an app. The apps add what a browser can't do well:

| | Electron | Android |
|---|---|---|
| Setup screen with a connection test | yes | yes |
| HTTPS with a self-signed certificate (pinning) | yes | yes |
| Plays after a restart while the server is down | yes (page snapshot and media cache) | yes (page snapshot and media cache) |
| Full screen, blocked keys, keep-awake | yes | yes (immersive mode, remote keys blocked) |
| Starts by itself | Windows login item, XDG autostart for the Linux AppImage | Home-app mode, or a boot receiver |
| Restarts after a crash | renderer crash and hang watchdogs | WebView renderer crash handler |
| Reported to the admin as | `electron-3.0.0` and the PC name | `android-3.0.0` and the device model |
| Unlock PIN to leave the player | all Ctrl+Alt shortcuts | the hidden exit gesture |
| Screen rotation | page (CSS) | the device itself; page (CSS) on TV boxes |
| Screenshot of the real screen | yes | yes |

You can still use a plain browser in kiosk mode (see [deployment.md](deployment.md#kiosk-browser-on-the-screen)).
The apps are better when screens restart while the network is down, or when the server uses a
self-signed certificate.

## Install

**Windows:** run `AiSignX Player Setup 3.0.0.exe` (see [Build](#build)). It installs for the
current user and starts at once. The installer is not code-signed yet, so Windows SmartScreen
asks for confirmation (**More info > Run anyway**).

**Android:** install the APK, for example with `adb install -r app-release.apk`, or copy it to
the device and allow installs from that source. A signed release build is needed for normal
use; debug builds install next to it as `com.aisignx.player.debug`.

Both apps use the application ID `com.aisignx.player`, the same as v2. Installing v3 replaces
the v2 app. v3 does not reuse v2's settings, so each screen is set up and paired again.

## Set up a screen

1. Start the app. The first start opens setup.
2. Enter the server address, for example `192.168.1.20:5000` or `https://signage.example.lan`.
   `http://` is added when you leave out the scheme. Paths, query strings and user names are
   rejected.
3. Press **Test**. The app calls `/healthz` on the server and shows one of:
   - **Connected to an AiSignX server**
   - **This address is not an AiSignX server**
   - **Can't reach the server**, with the reason
   - a certificate card, when the server uses HTTPS with a certificate the device doesn't
     trust (see [HTTPS](#https-and-certificate-pinning))
4. Plain `http://` to a public (non-private) address shows a warning but is allowed. On a LAN
   address there is no warning.
5. Press **Save and start**. The player opens full screen and shows a pairing code.

No password, token or code is ever typed on the device.

### Back to setup

- **Electron:** press **Ctrl+Alt+S**.
- **Android:** hold OK (D-pad center) or Menu on the remote for 1.5 seconds, or tap the top-left
  corner of a touch screen 7 times within 3 seconds, then confirm.

When an admin set an unlock PIN for the display, these ask for it first (see
[Display options](#display-options)).

Changing the server, or **Forget server**, clears the pairing cookie, the player's local
storage, both caches, the certificate pin and the unlock PIN. The client ID stays, so the screen keeps its
identity on the device.

## Pairing

Pairing is the same as in a browser:

1. The screen shows a six-character code and the address to open, for example
   `http://192.168.1.20:5000/displays/pair`.
2. In the admin, go to **Displays > Pair a display**, enter the code, name the screen and choose
   a default playlist.

The screen starts playing within a few seconds. The admin shows the player version
(`electron-3.0.0` or `android-3.0.0`) and the device name. The credential is an HttpOnly cookie;
the apps write it to disk right after pairing, so a power cut does not lose it.

A screen asks for a new code only when the server rejects its credential (the display was
unpaired or deleted). App-side errors such as a lost network or a changed certificate never
cause a new pairing.

**Server address changes:** on both apps, saving a different server address in setup counts
as changing the server (see **Back to setup**): the pairing cookie, local storage, both caches
and the certificate pin are cleared, and the screen shows a new pairing code. Pair it again in
the admin; the old display entry can then be deleted. To avoid this, give the server a DHCP
reservation or a DNS name, so its address never changes.

## HTTPS and certificate pinning

The apps trust a server certificate in this order:

1. A certificate from a public CA, or from a CA installed on the device. Electron uses its
   built-in CA list plus the Windows certificate store. Android uses the system and user
   certificate stores.
2. Otherwise, a certificate you pinned in setup.

With a self-signed certificate, **Test** shows a card with the certificate's SHA-256
fingerprint, its name and its expiry date. Print the fingerprint on the server and compare:

```bash
openssl x509 -noout -fingerprint -sha256 -in cert.pem
```

Tick **Trust this certificate** only if both match, then save. From then on the app accepts
exactly that certificate, and only for this server. Web pages in playlists always use normal
certificate checks.

If the server's certificate changes (renewed or replaced), the app refuses it and shows
**Server certificate changed. Re-run setup.** A screen that was online before keeps playing its
cached content. Run setup again and compare the new fingerprint. A changed certificate is never
accepted automatically.

Recommendations:

- Use a certificate with a long lifetime, or better, your own CA installed on all screens.
  Then certificates can be renewed without visiting each screen.
- A certificate issued by a private CA cannot be pinned on Electron when the server doesn't
  send the whole chain. Install the CA in the Windows certificate store instead.
- The apps never turn certificate checks off.

## Offline behaviour

| Situation | What happens |
|---|---|
| The server stops while playing | Playback continues from the last playlist. Media comes from the app's cache. Live updates reconnect by themselves. |
| The app starts while the server is down, after it was online before | The saved copy of the player page loads and plays cached media. |
| The app starts and has never reached the server | An offline screen retries every 10 seconds. |
| Media not cached yet, server online | Streamed from the server and downloaded in the background (2 at a time, videos first). |
| Media not cached yet, server offline | Skipped after 2 seconds. |
| Web pages in a playlist | Need the network. |
| Emergency messages | Follow the player's normal rules; an emergency stays on screen while offline. |

The media cache keeps each file after a checksum and size check. It uses at most 10 GB on
Electron and 4 GB on Android, and never more than about half the free disk space. Files in the
current playlist are never removed; others are removed least recently used first. Media of a
schedule that hasn't played since the screen went offline is not cached.

## Kiosk mode and autostart

### Electron (Windows)

- The player window is full screen, frameless and always in kiosk mode.
- Reload, DevTools, close, zoom and back-navigation keys are blocked. Admin keys (each asks for
  the unlock PIN first when one is set):

  | Keys | Action |
  |---|---|
  | Ctrl+Alt+S | Open setup |
  | Ctrl+Alt+L | Open the log file |
  | Ctrl+Alt+D | Open DevTools (detached) |
  | Ctrl+Alt+Q | Quit |

- Only one copy runs; a second start focuses the first. The display is kept awake.
- A crashed page reloads after 2 seconds (more than 5 crashes in 10 minutes restarts the app);
  a hung page is reloaded.
- The installed app starts at Windows logon (a checkbox in setup).
- With **Lock player** on, Alt+F4, closing and minimizing the window are blocked, and the
  window stays on top and takes the focus back. Electron cannot block Alt+Tab, the Windows key
  or Ctrl+Alt+Del; the app only brings itself back to the front. For a real lockdown, see
  [kiosk-lockdown.md](kiosk-lockdown.md).
- With **Player controls** on, the arrow, Page, Space, Enter and media keys control playback.
  Otherwise they stay blocked.

Set up the PC for unattended use:

- A dedicated Windows user with automatic logon.
- Power plan: turn off display sleep and computer sleep.
- Turn off notifications, and Windows Update restart prompts (active hours or managed updates).

Data lives in `%APPDATA%\AiSignX Player\v3`: `config.json`, `client_id.txt`, the log
`aisignx-player.log` (at most 2 MB, never contains cookies), `page-cache\` and `media\`.

### Android

- Immersive full screen, the screen stays on, and the back key is ignored. With **Player
  controls** off, remote keys are consumed so videos cannot be skipped or seeked. With it on,
  the D-pad, OK and media keys control playback (see `docs/player-api.md`, Player controls).
- With **Lock player** on, the app runs in lock task mode. Without Device Owner this is plain
  screen pinning (App pinning must be on, and a person can unpin with Back + Overview); as
  Device Owner it is a full lockdown with no Home, Recents or status bar. See
  [kiosk-lockdown.md](kiosk-lockdown.md).
- **Home-app mode (recommended for signage boxes):** tick **Use as Home app** in setup, press
  Home once and choose AiSignX Player as the default ("Always"). The player then starts after
  every boot and whenever Home is pressed.
- Without Home mode the app tries to start at boot. Android 10 and newer block app starts from
  the background, so this often does not work; use Home mode.
- The log is in `Android/data/com.aisignx.player/files/logs/` (1 MB, one rotation, never contains
  cookies).

Device Owner lock task mode is described in [kiosk-lockdown.md](kiosk-lockdown.md).
Automatic app updates are planned for a later version.

## Display options

An admin or editor sets these per display under **Displays > Edit**. They reach the screen with
the next manifest, within about a second.

- **Orientation** (0, 90, 180 or 270 degrees) is for screens mounted on their side or upside
  down whose device cannot rotate its own output. Android phones and tablets rotate natively;
  Android TV boxes and Electron rotate the page with CSS.
- **Volume** (0-100) applies to the player's own videos. It does not change the OS volume.
- **Player controls** (admins only) are off by default: touches, clicks, keys and the mouse
  wheel are ignored. Android still counts the 7 corner taps for the hidden exit. When on, tap,
  Space or OK pauses and resumes; the arrows seek 10 s in a video or change item; a swipe,
  PageUp/PageDown or the remote's up/down change item. A transparent layer covers the content,
  so a web page in a playlist cannot be clicked or scrolled from the screen either way.
  Paused time does not count in proof of play.
- **Resume after a pause** (admins only, 1-60 minutes, default 5): a paused screen always
  resumes after this time. An emergency message also resumes it.
- **Lock player** (admins only, Windows and Android apps): the app cannot be left without the
  unlock PIN, so the PIN must be set first, and it cannot be removed while the lock is on. A
  plain browser cannot be locked. See [kiosk-lockdown.md](kiosk-lockdown.md).
- **Remote unlock** (admins only): on the display page of a locked display, **Unlock remotely**
  lifts the lock for 5 minutes to 1 hour, so someone at the screen can close the app or use the
  admin shortcuts without the PIN. **Lock again** ends it early. It is sent live only: a screen
  that is not connected at that moment does not get it, and restarting the app locks it again.
- **Unlock PIN** (admins only, 4-8 digits) guards leaving the app:
  - **Electron:** every Ctrl+Alt shortcut (setup, log, DevTools, quit) asks for it.
  - **Android:** the hidden exit (long press or 7 taps) asks for it instead of yes/no.

  After 5 wrong PINs the prompt locks for 60 seconds, then twice as long after each further
  wrong PIN, up to 15 minutes. The lock survives a restart of the app. The app keeps only a
  salted PBKDF2 hash of the PIN, saved so an offline start still asks for it. A 4-8 digit PIN can
  be guessed offline by someone who copies that hash from the device or the manifest, so treat it
  as a lock against passers-by, not as a password.
- **Screenshot:** on the display page, **Screenshot** asks the screen for a picture. The apps
  capture the real screen (Electron `capturePage`, Android `PixelCopy`); a video on a hardware
  overlay may still come out black. A plain browser draws only its own media (no web page
  content). The picture appears on the display page within a few seconds.
- **Identify** shows the display name on the screen for 15 seconds. **Diagnostics** on the
  display page shows the screen size, the app version and capabilities, cache size, free
  storage and the last player errors.

Known limit on Android: the setup screen is the app's launcher activity, so anyone who can send
intents to the device (adb, or another installed app) can open it without the PIN. Someone
standing at the screen cannot.

## Build

### Electron

Needs Node.js 22.15 or newer (24 recommended).

```powershell
cd clients\electron-client
npm ci                 # exact versions from package-lock.json
npm test               # unit tests, plain Node
npm start              # run the app (opens setup on first run)
npm run smoke          # with a server on http://127.0.0.1:5000: checks the player reaches pairing
npm run dist:win       # dist-build\AiSignX Player Setup 3.0.0.exe
npm run dist:linux     # AppImage and deb (run on Linux; not tested yet)
```

If Electron did not download during `npm ci`, run `node node_modules\electron\install.js` once.
If electron-builder fails to extract `winCodeSign` with a symlink error, turn on Windows
Developer Mode.

### Android

Needs JDK 17 and the Android SDK (platform 34, build-tools 34). Set `ANDROID_HOME`, or create
`local.properties` with `sdk.dir=...` (it is ignored by git).

```powershell
cd clients\android-client
.\gradlew.bat testDebugUnitTest assembleDebug lintDebug
# app\build\outputs\apk\debug\app-debug.apk
.\gradlew.bat assembleRelease
# app\build\outputs\apk\release\app-release-unsigned.apk, or a signed APK with keystore.properties
```

To sign releases, copy `keystore.properties.example` to `keystore.properties` and fill it in.
**Use the same signing key forever:** Android only installs updates signed with the key of the
installed app. Never commit the keystore or `keystore.properties`.

## For developers

The apps talk to the page through a small hook, described in
[player-api.md](player-api.md#shell-hook). It passes device information, capabilities, stats and
screenshots in, and state and manifest notices out. No token or cookie ever passes through it. Each client folder has a
README with its internal layout and security notes.
