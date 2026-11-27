# Downloads page: install the apps from your own server

**Downloads** (in the menu, and in the user menu) lists the Windows player, the Android app and the PowerPoint
add-in, with version, size, date added and SHA-256. Anyone signed in can download (any role); admins add and remove
files. The page is also where the server gets the installers it hands to screens: the Android provisioning QR code
and install link use the newest **release** build of the Android app listed here (the newest Android app when there
is no release build), and **Push the update** on a screen uses the newest installer of its kind; for an Android app
that reported its signing key, the newest one signed with that key ([updates.md](updates.md)). So a debug and a
release build can be listed side by side, and each screen updates from its own.

## Files for the Windows set-up script

The **Set up a Windows PC** page needs two files here: the setup program (kind *Windows player app*, player and agent in one) and, as kind
*Windows agent*, your public certificate `*.cer`. Upload them as
usual: the kind is detected from the file name. See [windows-agent.md](windows-agent.md).

## Put a file there

- **In the browser (admin):** Downloads > *Add a file*, choose the file. The default, *Detect automatically*,
  works out what it is and its version; you can set the kind and version yourself and add a note. File types
  allowed: `exe`, `msi`, `zip`, `apk`, `vsto`, `pdf`, `dmg`, `pkg`, `deb`, `rpm`, `sh`, `ps1`, `txt`, `md`. The
  size limit is `AISIGNX_MAX_UPLOAD_MB`.
- **From the command line** (after building):
  `flask --app wsgi downloads add path\to\AiSignX-Player.apk --kind android-player --version 3.0.3`
  `--kind` is `auto` (the default) or one of `windows-player`, `android-player`, `powerpoint-addin`, `other`;
  `--version` and `--notes` are optional. A file of the same kind and name replaces the older entry, and an APK
  also replaces the older APKs signed with the same key (`--keep` keeps them all). A file that an update still in
  progress is downloading is kept, with a note; delete it later. The browser upload never replaces anything: a new
  entry is added, and the newest version is the one that counts.

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
type (debug or release) on a device; to switch, uninstall first. A fleet with both kinds updates fine as long as
both builds of a version are on this page ([android-kiosk.md](android-kiosk.md), "Signing keys").

Only the owner's own Galaxy Tab and a Shield have been used to try these steps. Other devices are untested.

## Windows player and PowerPoint add-in

Download the installer (or the add-in zip) from the page and run it on the PC. The Windows installer is not
code-signed unless you sign your own build, so SmartScreen shows a warning ("More info" > "Run anyway"); see
[clients.md](clients.md). For the add-in see [powerpoint-addin.md](powerpoint-addin.md).

## "Windows protected your PC" (SmartScreen)

A setup program downloaded in a browser is marked as coming from the internet, and Windows SmartScreen warns about any program
whose publisher it does not know. The installer is signed, but with **your own** certificate (free), which Windows has never
heard of, so it shows the blue "Windows protected your PC" page. It is a warning about the publisher, not a sign of a problem.

- **Once per file:** click **More info**, then **Run anyway**. Or right-click the downloaded file > Properties > tick **Unblock**.
- **No warning at all:** get the file onto the PC without the browser mark: the generated **Set up a Windows PC** script downloads it
  itself (a script download carries no mark), or copy it over the network or on a USB stick.
- **Make this PC trust your certificate** (stops "unknown publisher" prompts for everything you sign; run in an elevated PowerShell):
  `Import-Certificate -FilePath .\AiSignX-signing.cer -CertStoreLocation Cert:\LocalMachine\TrustedPublisher`
  and the same into `Cert:\LocalMachine\Root` (a self-signed certificate must be trusted as a root as well). Only do this on PCs you own.
- **To remove the warning for everyone:** a certificate from a publisher Windows trusts. The paid ways are an OV/EV code-signing
  certificate or Microsoft Trusted Signing. A free way for an open-source project is the **SignPath Foundation** programme (it signs
  builds of public open-source projects with its own certificate); SmartScreen then still learns a new file's reputation over time.
