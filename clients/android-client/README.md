# AiSignX Player for Android

A kiosk shell around the AiSignX v3 browser player. The app shows `<server>/player/`
in a full-screen WebView. Pairing, playback, schedules, emergency messages and live
updates all run in the player page, the same code a desktop browser uses. The app adds:

- a setup screen (server address, connection test, certificate trust),
- an offline snapshot of the player page and its scripts,
- a cache for image and video files, so playback continues while the server is down,
- full screen, keep-awake, restart after a renderer crash,
- remote keys as player controls (or blocked, when the display does not allow input),
- Lock player: Android lock task, with full lockdown as Device Owner,
- an optional Home-app mode and a start-at-boot fallback.

Android 8.0 (API 26) or newer. Phones, tablets and Android TV boxes.

## Build

Needs JDK 17 and the Android SDK (platform 34, build-tools 34). Set `ANDROID_HOME`, or
create `local.properties` with `sdk.dir=...` (it is gitignored).

```powershell
cd clients\android-client
.\gradlew.bat testDebugUnitTest assembleDebug lintDebug
# app\build\outputs\apk\debug\app-debug.apk  (application ID com.aisignx.player.debug)

.\gradlew.bat assembleRelease
# app\build\outputs\apk\release\app-release-unsigned.apk without keystore.properties
```

Install on a device with `adb install -r app\build\outputs\apk\debug\app-debug.apk`.

### Release signing

Copy `keystore.properties.example` to `keystore.properties` and fill it in; the release
build is then signed. **Use the same key forever.** Android only installs an update that
is signed with the key of the installed app; a lost key means uninstalling and setting up
every device again. Never commit the keystore or `keystore.properties`.

The release application ID is `com.aisignx.player`, the same as v2, so a v3 release signed
with the v2 key upgrades a v2 install in place. v3 ignores v2's settings and deletes v2's
media cache on first start; the screen is set up and paired again.

## Setup on the device

1. Open **AiSignX Player**. Enter the server address, for example `192.168.1.20:5000` or
   `https://signage.example.lan`, and press **Test**.
   - "Connected to an AiSignX server": press **Save and start**.
   - A certificate card appears when the server uses https with a certificate this device
     does not trust (self-signed). Compare the SHA-256 fingerprint with the one printed on
     the server: `openssl x509 -noout -fingerprint -sha256 -in cert.pem`. Tick **Trust this
     certificate** only if they match. The pin applies to this server only.
   - Plain http to a public address shows a warning. On a LAN it is silent.
2. The player shows a pairing code. In the admin, open **Displays > Pair** and enter it.
   The admin shows the player as `android-3.0.0` with the device model.
3. No password, token or code is typed on the device.

**Back to setup:** hold the remote's OK (D-pad center) or Menu key for 1.5 seconds, or tap
the top-left corner of a touch screen 7 times within 3 seconds, then confirm.
**Forget server** clears the cookie (the display credential), web storage, both caches and
the certificate pin. The client ID stays.

Changing the server address (also its IP or port) means pairing the screen again, because
the credential belongs to the server's origin. Give the server a DHCP reservation or a DNS
name.

## Kiosk tablet from a QR code (factory reset to locked down)

A factory-reset tablet can be set up with no cable: **Admin > Downloads > Set up an Android
tablet** makes a QR code; on the tablet's Welcome screen tap 6 times and scan it. The app
installs, becomes Device Owner, pairs itself and shows the playlist. The display's **Lock player**
switch then applies or removes the full lockdown (status bar, keyguard, Home, safe boot, factory
reset button, ...) so the web page can always give the tablet back. Everything about it, the
Samsung notes and a test checklist: [docs/android-kiosk.md](../../docs/android-kiosk.md).

Code map: `provision/` (QR set-up and pairing claim), `ui/Lockdown.kt` (what the lockdown does,
unit tested) and `ui/LockdownApplier.kt` (the DevicePolicyManager calls), `shell/SignedCommand.kt`
and `shell/Ed25519.kt` (signed commands; the pure-Java `net.i2p.crypto:eddsa` library, because
the JDK has Ed25519 only from API 33), `shell/Installer.kt` (PackageInstaller).

## Home-app mode and starting at boot

For dedicated signage boxes tick **Use as Home app** in setup, then press Home once and
choose AiSignX Player as the default ("Always"). The player then starts after every boot
and after Home is pressed.

Without Home mode the app relies on a `BOOT_COMPLETED` receiver. Android 10 and newer block
activity starts from the background, so on many devices that start is refused; Home mode is
the reliable option.

Updates: the admin can send **Update app** from the display page (signed command, see
[docs/signed-commands.md](../../docs/signed-commands.md)). As Device Owner the install is silent;
otherwise Android asks someone to confirm it.

## Player controls

When the display's **Player controls** option is on, touch goes to the player page and the
remote (or a keyboard) controls playback:

| Key | Action |
|---|---|
| OK (short press), Enter, Space, Play/Pause | pause / resume |
| D-pad right / left, Fast forward / Rewind | +10 s / -10 s in a video; next / previous item otherwise |
| D-pad down / up, Channel down / up, Page down / up, Next / Previous | next / previous item |
| Play, Pause, Stop | play, pause |
| Mouse back / forward button | previous / next item |

With the option off these keys are swallowed. Holding OK or Menu for 1.5 s still opens the
hidden exit either way. Volume, power and Home always pass through.

## Lock player

With **Lock player** on (the admin requires an unlock PIN for it) the app starts Android
lock task:

- **Without Device Owner** this is screen pinning. Android asks once ("Pin this app?"), and
  App pinning must be enabled in Settings > Security. A person can unpin with Back+Overview.
- **As Device Owner** lock task starts without a prompt, Home, Recents and the status bar are
  gone, the power menu stays, and the app becomes the persistent Home app. Provision a fresh
  or factory-reset device with no Google account, enable USB debugging, then:

  ```powershell
  adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver
  # debug build:
  adb shell dpm set-device-owner com.aisignx.player.debug/com.aisignx.player.AisignxDeviceAdminReceiver
  ```

  Devices provisioned with the v2 app keep working (same receiver name). To undo, leave the
  player through the PIN-protected exit and press **Remove Device Owner** in setup, or
  factory-reset the device.

### Hiding the "App is pinned" message

The "App is pinned" message and the "Pin this app?" prompt are Android's own security
notices for screen pinning. An app cannot hide them. They disappear only when the app is
the **Device Owner** (see above): lock task then starts silently, with no message, no prompt
and no way to unpin.

Android allows a Device Owner only on a device with **no accounts**: no Google, Samsung or
other account may be signed in. On a personal phone the command fails with
`Not allowed to set the device owner because there are already some accounts on the device`.
So:

- **Dedicated signage device or TV box:** factory-reset it, skip every account sign-in during
  setup, enable USB debugging, install the app, run the `dpm set-device-owner` command above,
  then turn on **Lock player** in the admin. No pinned message appears.
- **Personal or test phone:** keep screen pinning and accept the message. Locking still
  works; only the notice stays.

Check the result with `adb shell dumpsys device_policy` (look for `Device Owner`), or in the
display's diagnostics (`lock: owner` instead of `lock: pinned`).

The hidden exit asks for the PIN and leaves lock task; the player locks again when it
starts. **Unlock remotely** on the display page lifts the lock for the chosen time and lets
the hidden exit skip the PIN; **Lock again** ends that early. A restart always locks again.
The heartbeat diagnostics show the lock state (`off`, `pinned`, `owner`, `unlocked`, `locked`
when pinning was declined, or `failed`). See `docs/kiosk-lockdown.md`.

## Offline behaviour

| Case | Result |
|---|---|
| Server stops while playing | Playback continues from the player's saved manifest and the media cache. Live updates reconnect by themselves. |
| Cold start, server down, was online before | The page snapshot loads and plays cached media. |
| Cold start, never online | An offline screen retries every 10 seconds. |
| Media not cached yet | Streamed from the server while it downloads in the background (2 at a time, videos first). |
| Server certificate changed | Refused. Playback continues from the snapshot if there is one; otherwise "Server certificate changed. Re-run setup." |

The media cache uses at most 4 GB or half of the free space, whichever is smaller. Files
in the current playlist are never evicted; others are evicted least recently used first.

Media for a schedule that has not played since the server went away is not cached.

## Device agent

A small agent inside the player app. Everything is **off by default** and switched per display in the
admin (Displays > display > Edit). The app reports what it can do (`stats().agent`, cap `agent`);
the admin page hides the rest. Design: `docs/device-agent-design.md`.

- **Watchdog** (switch "watchdog"). The page sends `alive` about every 10 s. If it stops, the app
  escalates: 45 s reload the page, 90 s recreate the WebView, 180 s recreate the activity, 300 s
  restart the process. The last two together at most once per hour; after that the app logs it and
  reports `gave_up`. Time does not count while the screen is off, the app is paused, the offline
  page is showing, or in the first 60 s after a page load.
- **Restart player** (switch "restart") and **Collect logs** (switch "logs"). While a switch is on and
  the player is running, the app asks the server for commands about every 20 s with
  `Authorization: Bearer <display token>` over its normal (pinned) TLS connection. The token is the
  HttpOnly `aisignx_display` cookie, read from the WebView cookie store, kept only for one request,
  never logged. Only the verbs `restart_player` and `collect_logs` exist; anything else is answered
  "unsupported", a switched-off verb "switched off". Each command id is stored before the command
  runs, so it runs at most once. `restart_player` acks first, then recreates the activity (and
  restarts the process only with Device Owner or Home mode). `collect_logs` redacts tokens, cookies,
  certificate pin, PIN hash and config values, keeps the newest part (gzip at most 900 KB) and uploads it.
- **Crashes.** An uncaught exception is written to the log; with the watchdog on, the app asks for a
  relaunch (at most once per 10 min). At start the previous exit reasons (Android 11+) are logged.
- **Update.** After an app update the player is started again, but only with Home mode or Lock player on.

### Honest limits

- Without **Device Owner** or **Home mode**, Android 10+ may refuse to start the app from the
  background. A process restart, a crash relaunch and the update start then can leave the screen dark
  until someone opens the app. That is why `restart_player` only recreates the activity on a plain device.
- The relaunch alarm is inexact (a few seconds late, more in Doze) because exact alarms need a
  permission Play policy restricts.
- A user's **Force stop** cannot be undone by the app; nothing runs until it is opened again.
- No reboot and no app update in this version (`can_reboot` and `can_update` are false).
- Not added: `LOCKED_BOOT_COMPLETED` (settings live in credential-protected storage, which is
  unreadable before the first unlock) and `QUICKBOOT_POWERON` (vendor-specific, unverified).
- The watchdog relies on the page sending `alive`; a server older than this version never does, so
  its switch is off.

### Manual test checklist (real device; nothing here was run on hardware yet)

Do it on an Android TV box (Shield TV) and on a phone. Debug builds log to the file under "Logs".

1. Switches off: no `GET /commands` in the server log, no restart, whatever you send.
2. Switch "logs" on, press Collect logs: the display page offers a download within ~30 s; open the
   gzip: no token, cookie, PIN hash or certificate pin; the newest lines are present.
3. Switch "restart" on, press Restart: the player comes back by itself (check the screen is not dark);
   the command shows as done once. Repeat in Home mode and (if available) as Device Owner.
4. Switch "restart" off, send Restart: it fails with "switched off".
5. Watchdog on: block the page's timers (Chrome remote debugging on a debug build, pause in
   DevTools, or `adb shell am` to freeze) and watch the log: reload at ~45 s, WebView at ~90 s,
   activity at ~180 s; a second heavy step within the hour reports `gave_up`.
6. Turn the screen off for 10 minutes (TV standby): no watchdog action on wake.
7. Unplug the network: the offline page shows, no watchdog action.
8. Crash: with the watchdog on, force a crash (debug only) and check the stack in the log and the relaunch
   (plain device: note whether Android allowed it).
9. Install an update over the app (`adb install -r`) in Home mode: the player starts; on a plain
   device nothing starts.
10. Force stop in Settings: confirm nothing restarts (expected).
11. Heartbeat diag on the display page shows the agent tier, `watchdog`, `restarts_1h`, `gave_up`.

## Logs and debugging

The log is at `Android/data/com.aisignx.player/files/logs/aisignx-player.log` (1 MB,
one rotation). It never contains cookies. Debug builds allow Chrome remote debugging at
`chrome://inspect`.

## Layout

```
app/src/main/java/com/aisignx/player/
  AiSignXApp.kt  Config.kt  ServerUrl.kt  FileLog.kt
  net/      Tls.kt (pinning trust manager, capture), Http.kt (OkHttp, /healthz)
  cache/    PageCache.kt  MediaCache.kt  MediaDownloader.kt  Range.kt  Served.kt
  shell/    ShellInfo.kt (window.AiSignXShellInfo)  ShellPort.kt (window.AiSignXShellPort)
            Agent.kt (watchdog, command poll)  AgentRules.kt (pure rules)  AgentStore.kt  AgentRestart.kt
  ui/       SetupActivity.kt  PlayerActivity.kt  PlayerWebClient.kt  Kiosk.kt  TvKeys.kt
            ControlKeys.kt (keys -> control actions)  LockPolicy.kt  LockTask.kt (lock task, Device Owner)
  AisignxDeviceAdminReceiver.kt   Device Owner receiver
  boot/     BootReceiver.kt
app/src/test/   JVM unit tests (no emulator needed)
```
