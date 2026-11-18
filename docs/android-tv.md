# Android TV boxes and phones that are already set up

The factory-reset QR set-up (see [android-kiosk.md](android-kiosk.md)) suits a **tablet** you can wipe. An
**NVIDIA Shield**, a Chromecast with Google TV, a Fire TV, or a phone you already use are set up the **normal way**:
install the app, pair with a short code. They work as normal screens: playlists, schedules, emergency messages,
restart-player and log collection from the web page all work.

## Install and pair

1. In the admin open **Downloads > Set up an Android tablet** and press **Make an install link** (bottom card).
   You get a short address such as `192.168.1.19:5000/get/K7M2Q9XD` and a QR code for it.
2. On the device:
   - **Phone:** scan the QR code with the camera, press **Download the app**, allow installs from the browser when
     Android asks, open the file and press Install.
   - **TV box:** install a "Downloader" style app from the store, type the short address with the remote (the
     code is not case sensitive), press **Download the app**, then Install.
3. Open the app, type the server address shown on that page, press **Test**, then **Save**. The app shows a short
   pairing code.
4. In the admin: **Displays > Pair a display**, type the code.

## Leaving the player (the hidden exit)

The player hides its way out so a passer-by cannot leave it. Use whichever your device has:

| Device | How |
|---|---|
| **Phone or tablet (touch)** | Tap the **top-left corner 7 times within 3 seconds**, then enter the unlock PIN |
| **TV box with a remote** | **Hold OK (the centre button) or Menu for about 1.5 seconds**, then enter the PIN with the remote |

The exit leads to the setup screen, which has **Android settings** and **Wi-Fi settings** buttons.

## What a TV box cannot do (unless set up with adb)

A normal (non Device Owner) device cannot be locked down completely, rebooted from the web page or updated silently:
Android asks a person to confirm an install. To get those features on a Shield, make the app the Device Owner with adb
(see [kiosk-lockdown.md](kiosk-lockdown.md)); that only works on a device with no accounts signed in.

## Updating the app

Upload a newer Android app (higher version) on **Downloads**. A screen running an older version shows **Update
available** on its display page. On a Device Owner device **Push the update** installs it silently; on a normal
device the TV or phone shows Android's install prompt for someone to confirm.
