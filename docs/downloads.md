# Downloads page: install the apps from your own server

**Downloads** (in the menu, and in the user menu) lists the Windows player, the Android app and the PowerPoint
add-in, with version, size, date added and SHA-256. Anyone signed in can download (any role); admins add and remove
files. The page is also where the server gets the installers it hands to screens: the Android provisioning QR code
and install link use the newest Android app listed here, and **Push the update** on a screen uses the newest
installer of its kind ([updates.md](updates.md)).

## Put a file there

- **In the browser (admin):** Downloads > *Add a file*, choose the file. The default, *Detect automatically*,
  works out what it is and its version; you can set the kind and version yourself and add a note. File types
  allowed: `exe`, `msi`, `zip`, `apk`, `vsto`, `pdf`, `dmg`, `pkg`, `deb`, `rpm`, `sh`, `ps1`, `txt`, `md`. The
  size limit is `AISIGNX_MAX_UPLOAD_MB`.
- **From the command line** (after building):
  `flask --app wsgi downloads add path\to\AiSignX-Player.apk --kind android-player --version 3.0.3`
  `--kind` is `auto` (the default) or one of `windows-player`, `android-player`, `powerpoint-addin`, `other`;
  `--version` and `--notes` are optional. A file of the same kind and name replaces the older entry
  (`--keep` keeps both). The browser upload never replaces anything: a new entry is added, and the newest
  version is the one that counts.

How the kind and version are found:

- `.apk`: the kind is always *Android player app*, and the version is read exactly from inside the file
  (any app, not only AiSignX; the provisioning page checks that the package is AiSignX before it uses it).
- `.exe` or `.msi` with "player" or "aisignx" in the name: *Windows player app*, version from the file name
  (the installer is named like `AiSignX Player Setup 3.1.0.exe`).
- `.zip` with "powerpoint" or "add-in" in the name: *PowerPoint add-in*, version from the name.
- Anything else is *Other*. If no version can be found the page warns you; add one by hand, because a file with
  no version cannot be chosen as "newest" and cannot be pushed to screens.

Files are stored with a random name in the **downloads** folder next to `uploads` (for example `server/downloads`,
`C:\ProgramData\AiSignX\downloads`, `/data/downloads` in Docker); the browser only gets the original file name.
The folder is **not part of a backup** ([backup.md](backup.md)): after a restore onto a new machine, upload the
files again.

## Install on an Android phone or Shield

1. In the device's browser open `http://<server>:5000/downloads/` and sign in.
2. Tap the APK. Android asks once to allow installs from that browser; allow it.
3. Open the app, enter the server address, pair with the short code.

An easier route for phones and TV boxes: on **Downloads** press *Set up an Android tablet as a kiosk (QR code)*
(admin), then under *Phones that are already set up, and Android TV boxes* make an **install link**. It is a short
address (`/get/<code>`) and a QR code, valid 7 days, that opens a public page with a download button and the
server address; making a new link replaces the old one. It carries no way to pair a screen by itself. The
tablet QR code on the same page needs a factory-reset device; see [android-kiosk.md](android-kiosk.md) and
[android-tv.md](android-tv.md).

Android only lets an update replace an app that was signed with the same key, so keep using the same build
type (debug or release) on a device; to switch, uninstall first.

Only the owner's own Galaxy Tab and a Shield have been used to try these steps. Other devices are untested.

## Windows player and PowerPoint add-in

Download the installer (or the add-in zip) from the page and run it on the PC. The Windows installer is not
code-signed unless you sign your own build, so SmartScreen shows a warning ("More info" > "Run anyway"); see
[clients.md](clients.md). For the add-in see [powerpoint-addin.md](powerpoint-addin.md).
