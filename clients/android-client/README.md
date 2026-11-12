# AiSignX Player for Android

A kiosk shell around the AiSignX v3 browser player. The app shows `<server>/player/`
in a full-screen WebView. Pairing, playback, schedules, emergency messages and live
updates all run in the player page, the same code a desktop browser uses. The app adds:

- a setup screen (server address, connection test, certificate trust),
- an offline snapshot of the player page and its scripts,
- a cache for image and video files, so playback continues while the server is down,
- full screen, keep-awake, remote-key blocking, restart after a renderer crash,
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

## Home-app mode and starting at boot

For dedicated signage boxes tick **Use as Home app** in setup, then press Home once and
choose AiSignX Player as the default ("Always"). The player then starts after every boot
and after Home is pressed.

Without Home mode the app relies on a `BOOT_COMPLETED` receiver. Android 10 and newer block
activity starts from the background, so on many devices that start is refused; Home mode is
the reliable option.

Device Owner / lock-task mode, PIN unlock and automatic app updates are not part of this
version.

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
  ui/       SetupActivity.kt  PlayerActivity.kt  PlayerWebClient.kt  Kiosk.kt  TvKeys.kt
  boot/     BootReceiver.kt
app/src/test/   JVM unit tests (no emulator needed)
```
