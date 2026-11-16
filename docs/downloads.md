# Downloads page: install the apps from your own server

User menu > **Downloads** lists the Windows player, the Android app and the PowerPoint add-in, with version, size
and SHA-256. Anyone signed in can download; admins add and remove files.

## Put a file there

- **In the browser (admin):** Downloads > *Add a file*, choose the file and what it is.
- **From the command line** (after building):
  `flask --app wsgi downloads add path\to\AiSignX-Player.apk --kind android-player --version 3.0.2`
  Kinds: `windows-player`, `android-player`, `powerpoint-addin`, `other`. A newer file with the same name
  replaces the old one.

## Install on an Android phone or Shield

1. In the device's browser open `http://<server>:5000/downloads/` and sign in.
2. Tap the APK. Android asks once to allow installs from that browser; allow it.
3. Open the app, enter the server address, pair with the short code.

Android only lets an update replace an app that was signed with the same key, so keep using the same build
type (debug or release) on a device; to switch, uninstall first.

## Windows player and PowerPoint add-in

Download the installer (or the add-in zip) from the page and run it on the PC. The Windows installer is not
code-signed yet, so SmartScreen shows a warning ("More info" > "Run anyway"); see docs/clients.md.
