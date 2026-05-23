# AISignX Player - Android Client

Native Android kiosk display client. Uses native setup screens for server
configuration and device registration, then loads the AISignX player inside a
fullscreen WebView.

## Requirements
- Android Studio Hedgehog (2023.1.1) or newer
- Android SDK 34
- Java 17 / Kotlin 1.9

## Building

### Debug (no signing setup)

```bash
cd clients/android-client
./gradlew assembleDebug
./gradlew installDebug
```

### Release (signed APK)

1. Create a release keystore (once) and keep it **local** — keystores are gitignored.
2. Copy the template and fill in your values:

   ```bash
   cp keystore.properties.example keystore.properties
   ```

3. Place your `.keystore` file in `clients/android-client/` (or adjust `storeFile` in `keystore.properties`).
4. Build:

   ```bash
   ./gradlew assembleRelease
   # APK: app/build/outputs/apk/release/app-release.apk
   ```

Without `keystore.properties`, `assembleRelease` still builds an **unsigned** release APK suitable for local testing; use `assembleDebug` for day-to-day development.

Open `clients/android-client/` in Android Studio for GUI builds: **Build → Generate Signed Bundle/APK**.

## First-run flow
1. App launches -> native **Setup** screen
2. Enter the server URL -> app tests connectivity
3. Either enter setup values manually or import a downloaded setup JSON file
4. Enter a friendly display name and enrollment code -> app sends registration request
5. Native **Waiting** screen shows with Device ID
6. Admin approves in the server **Displays** page
7. App polls every 5s, detects approval, saves token, launches **Player**

## Kiosk behaviour
- Full-screen immersive, all system UI hidden
- Back/Menu keys blocked in PlayerActivity
- Screen kept on at all times (KEEP_SCREEN_ON flag)
- Launches automatically on device boot via BootReceiver
- Launcher intent-filter set so device can be configured as a dedicated kiosk launcher
- Long-press opens the in-player unlock PIN keypad when a display unlock PIN is configured
- Server-pushed Reload, Reboot App, and Update Client commands are handled natively
- Offline media cache uses a disk-backed WebView request interceptor for player assets and uploaded media

## Resetting
Tap **Reset Setup** on the Waiting or error screens to wipe config and return to Setup.

## Sideloading
Enable *Install from unknown sources* on the target device, then transfer the APK via ADB or USB:

```bash
adb install AISignX-Player.apk
```

## Publishing to AISignX

After building a release APK:

```powershell
Copy-Item "clients\android-client\app\build\outputs\apk\release\app-release.apk" `
          "server\static\clients\AISignX-Player.apk" -Force
```

Then update `server/static/clients/client_versions.json` or use the server Downloads
page Manifest Editor so `clients.android.version` is strictly higher than the
installed app.

## Auto-update

The Android client checks `/api/version` for the `clients.android` entry.
When the display setting **Auto-update client** is enabled, the player page
passes that flag to the native app and the app immediately checks for a newer
APK, then repeats every 15 minutes.

Android fully silent installs require the device to be provisioned as Device
Owner. On non-Device-Owner devices, AISignX can download and start the install,
but Android may still show the system approval prompt.

Provision a fresh/factory-reset device as Device Owner:

```powershell
adb shell dpm set-device-owner com.aisignx.player/.AisignxDeviceAdminReceiver
```

Notes:
- The installed app must include `AisignxDeviceAdminReceiver` (`1.4.6+`).
- APK updates must be signed with the same key as the installed app.
- Device Owner setup usually fails if Google accounts or another owner are
  already configured; factory reset first if needed.