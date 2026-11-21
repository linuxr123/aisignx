# Android TV boxes and phones that are already set up

The factory-reset QR set-up (see [android-kiosk.md](android-kiosk.md)) suits a **tablet** you can wipe. An
**NVIDIA Shield**, a Chromecast with Google TV, a Fire TV, or a phone you already use are set up the **normal way**:
install the app, pair with a short code. They work as normal screens: playlists, schedules, emergency messages,
restart-player and log collection from the web page all work.

## Install and pair: from your PC (recommended for a TV box)

Typing an address with a remote and the free "Downloader" apps are the weak part, so the admin makes a small script that
does it from your PC over the network with Google's `adb` tool.

**Once, on the TV box** (NVIDIA Shield, Chromecast with Google TV, most Android TV boxes):
1. Settings > Device Preferences > About: press **Build** seven times (it says "you are a developer").
2. Settings > Device Preferences > Developer options: switch on **Network debugging** (on some boxes: **USB debugging**).
3. Note the box's IP address (Settings > Network, or the router's list).

**Then, on your PC:**
1. In the admin open **Downloads > Set up an Android tablet** and press **Make an install link** (bottom card).
2. In the box *Android TV box: install from your PC* press **Download the PC script**.
3. Run it in PowerShell: `powershell -ExecutionPolicy Bypass -File .\Install-AiSignX-on-Android-TV.ps1` (it asks for the box's IP
   address; or add `-Address 192.168.1.40`).
4. The first time, the TV asks "Allow this computer?": choose **Always allow** and run the script again.

The script finds `adb` (or downloads it from Google), downloads the app **from your server and checks its SHA-256**,
installs it, and opens it with your server address already filled in. On the TV, wait for the green "connected" line and press
**Save**. It shows a short pairing code: type it in the admin under **Displays > Pair a display**.

Add `-DeviceOwner` to also make the app the Device Owner (silent updates, remote reboot, Home mode). Android only allows
that on a device with **no accounts signed in** (a Shield with a Google account refuses); the script says so and the app still
works as a normal app.

## Install and pair: with the link, no PC

If you cannot use a PC: the same **install link** page opens on the TV or a phone.

1. On a phone, scan the QR code beside the link; on a TV box, use any file-download or browser app and type the short address
   (for example `http://192.168.1.19:5000/get/K7M2Q9XD`, the code is not case sensitive).
2. Press **Download the app**, then **Install** (allow installs from that app when Android asks).
3. Open AiSignX, type the server address shown on the page, press **Test**, then **Save**, and pair with the code in the admin.

The free "Downloader by AFTVnews" app (package `com.esaba.downloader`, official page https://aftvnews.com/downloader) is one
such app, but the PC script above is more reliable.

## A phone or tablet you keep using: QR code, no factory reset

**Downloads > Set up an Android tablet as a kiosk**, fill in the name and playlist, and press **Make a QR code to install as an app (no reset)**
(the other button is the factory-reset kiosk code). Scan the code with the phone's camera: a page opens on your server with the app
download and an **Open AiSignX and pair** button. After installing, that button opens the app, which asks you to confirm
**Pair this device?** and then pairs itself (no code to type). The screen appears under Displays already paired.

- It is a normal app, not a managed device: no silent updates or reboots from the web page, and the Wi-Fi fields are not used.
- The QR code works for 24 hours and for one device; it holds the pairing secret, so do not share a screenshot.
- Only a device with **no server set up yet** takes the link, and only after you confirm: an installed player never changes server this way.
- Unit-tested (link parsing and the rules), not yet tried on a phone (testing checklist M7).

## Lock player on a TV box or phone (not Device Owner)

Without Device Owner, Android only offers *screen pinning*, which a TV box does not enforce reliably: on the NVIDIA Shield the Home button of a
mouse still left the player. From app 3.0.6, turning **Lock player** on in such a device **also makes AiSignX the Home app**, so the
Home button comes back to the player; leaving needs the hidden exit and the unlock PIN (below). The first time, Android may ask which Home
app to use: choose AiSignX and **Always**. This is covered by unit tests only; it has not been confirmed on a Shield (checklist M8).

## Leaving the player (the hidden exit)

The player hides its way out so a passer-by cannot leave it. Use whichever your device has:

| Device | How |
|---|---|
| **Phone or tablet (touch)** | Tap the **top-left corner 7 times within 3 seconds**, then enter the unlock PIN |
| **TV box with a remote** | **Hold OK (the centre button) or Menu for about 1.5 seconds**, then enter the PIN with the remote |

The exit leads to the setup screen. After the PIN it has one button per Android settings page (Wi-Fi, Display, Date and
time, Sound, Apps, About the device, main Settings page). On a device that is not Device Owner the setup screen does not
offer **Factory reset this device**.

## What a TV box cannot do (unless set up with adb)

A normal (non Device Owner) device cannot be locked down completely, rebooted from the web page or updated silently:
Android asks a person to confirm an install. To get those features on a Shield, make the app the Device Owner with adb
(see [kiosk-lockdown.md](kiosk-lockdown.md)); that only works on a device with no accounts signed in.

## Updating the app

Upload a newer Android app (higher version) on **Downloads** (kind **Android player app (APK)**). A screen running an older version shows **Update
available** on its display page. On a Device Owner device **Push the update** installs it silently; on a normal
device the TV or phone shows Android's install prompt for someone to confirm.
