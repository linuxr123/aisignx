# Android TV boxes and phones that are already set up

The factory-reset QR set-up (see [android-kiosk.md](android-kiosk.md)) suits a **tablet** you can wipe. An
**NVIDIA Shield**, a Chromecast with Google TV, a Fire TV, or a phone you already use are set up the **normal way**:
install the app, pair with a short code. They work as normal screens: playlists, schedules, emergency messages,
restart-player and log collection from the web page all work.

## Install and pair

1. In the admin open **Downloads**, press **Set up an Android tablet as a kiosk (QR code)** and scroll to the card
   **Phones that are already set up, and Android TV boxes**. Press **Make an install link**. You get a short address
   such as `192.168.1.19:5000/get/K7M2Q9XD` and a QR code for it. The link works for 7 days; a new link replaces the
   old one. The server needs an Android app on the Downloads page first (kind **Android player app (APK)**).
2. On the device:
   - **Phone:** scan the QR code with the camera, press **Download the app**, allow installs from the browser when
     Android asks, open the file and press Install.
   - **TV box:** install the app **Downloader** (developer **AFTVnews**, package `com.esaba.downloader`, official
     page <https://aftvnews.com/downloader>) from the store of the box. In Downloader type the **full address
     with `http://`** in front, for example `http://192.168.1.19:5000/get/K7M2Q9XD` (use `https://` if your server
     does). Without the scheme Downloader may treat the text as a search. The code is not case sensitive. Open the
     page, press **Download the app**, then Install. The first time, Android asks to allow Downloader to install
     apps: allow it.
   - **Optional, with adb:** if you would rather not type on a remote, download the APK from the admin's Downloads page
     to a PC, switch on network debugging on the box, then run `adb connect <box address>:5555` and
     `adb install -r app-release.apk`. The menu names for network debugging differ by box and are not described here.
3. Open the app, type the server address shown on that page, press **Test**, then **Save and start**. The app shows a
   short pairing code.
4. In the admin: **Displays > Pair a display**, type the code.

Only the NVIDIA Shield has been tried. Chromecast with Google TV and Fire TV follow the same route but are untested.

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
