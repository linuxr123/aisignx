# Android kiosk tablet

Turn a tablet (for example a Samsung Galaxy Tab) into a locked-down signage screen that you
control from the web page. The whole set-up is: **factory reset, scan one QR code, done.** No
adb, no cable, no typing on the tablet.

This is the managed route. The older manual route (adb) is in
[kiosk-lockdown.md](kiosk-lockdown.md); both end in the same state, the app as **Device Owner**.

This page describes the app version 3.0.3 (`versionCode` 303). Only one device has run it: the
owner's Samsung Galaxy tablet (SM-X210, Android 16). Other tablets, other Android versions and
other makers are untested; the notes below say what is confirmed and what is not.

## What you get

- The tablet installs the AiSignX app from your server, pairs itself and shows content.
- **Lock player** on the display page (Displays > Edit) locks the tablet down. **Lock player
  off** gives it back: you can use it, and factory reset it.
- With the admin's switches (Displays > Edit > Device agent) you can reboot it, change its
  autostart and update the app from the web page. These are signed commands, see
  [signed-commands.md](signed-commands.md).

## Set-up, step by step

1. In the admin open **Downloads** and press **Set up an Android tablet as a kiosk (QR code)**
   (admin role). Fill in **Name for this screen**, **Playlist**, and **Address the tablet will
   use to reach this server** (pre-filled with the server's LAN address when you opened the page
   as `localhost`). Two boxes are ticked by default: **Lock the tablet down as a kiosk** (sets
   Lock player on the new display) and **Allow restart, reboot, log collection and app updates
   from the web page** (switches on the Device agent options). Optional: Wi-Fi name, security
   and password, so the tablet can download the app. The server needs an Android app on the
   Downloads page, kind **Android player app (APK)**, with package `com.aisignx.player` (build it, see
   [clients/android-client/README.md](../clients/android-client/README.md)); the page
   uses the newest version.
2. Press **Make the QR code**. It is valid for 24 hours and for one tablet. It contains the
   pairing secret (and the Wi-Fi password if you typed one): do not share a picture of it.
3. **Factory reset the tablet** (Settings > General management > Reset > Factory data reset),
   or take it out of the box. Do not sign in to any account.
4. On the first **Welcome** screen tap the same spot **6 times**. A QR scanner opens (if it
   asks for Wi-Fi, connect to the same network as the server).
5. **Scan the QR code.** The tablet downloads the app from the server, checks it against the
   checksum in the code, installs it, makes it the Device Owner and starts it. It shows
   "Setting up this screen" for a moment, then your playlist. The screen appears in
   **Displays**, already paired.
6. Open the display and set an **unlock PIN**. The server requires a PIN before Lock player
   can be on. The QR page already ticks Lock player for the new display, so if you left that box
   ticked, set the PIN and save; if you unticked it, also turn on **Lock player**. The tablet
   applies the lockdown within a minute.

The server address in the QR code must be reachable from the tablet: use the server's LAN
address, not `localhost`. Plain `http://` on a LAN works (the tablet checks the download
against a checksum). For `https://` the certificate must be one the tablet trusts; a
self-signed certificate cannot be accepted without a person, so the player then starts with
its normal pairing code and you pair it in the admin as usual (and trust the certificate in
the app's setup). Other TLS errors, and a certificate that looks "not yet valid" because the reset tablet's
clock is still wrong, are retried. If the set-up has not finished after 10 minutes (wrong address, server
down), or if you tap the "Setting up this screen" text 7 times quickly and confirm, the app forgets the QR
address and opens the normal setup screen (where **Remove Device Owner** is also available).

## What the lockdown does

When the tablet is Device Owner and **Lock player** is on, the app sets (and the web page can
always undo, see below):

| Setting | Effect |
|---|---|
| Lock task allow-list = this app | The app starts pinned without a prompt; no Home, Recents or notifications (only the power menu stays, see below) |
| Status bar disabled, keyguard disabled | Nothing to pull down; no lock screen to swipe |
| Screen stays on while plugged in (AC, USB, wireless) | The tablet does not sleep on its charger. The earlier setting is saved and restored |
| This app is the persistent Home app | Home goes to the player, also after a reboot |
| Uninstall of this app blocked | |
| No safe boot, no adding users, no factory reset from Settings, no USB file transfer | The usual ways around a kiosk |

**Power menu.** The lockdown does not remove it. On the owner's Galaxy tablet, **Power + Volume
Down** opens the power menu, and shutting the tablet down from it works. The web page cannot
shut a tablet down; it can only reboot it (Device Owner, once per 10 minutes).

Not set on purpose: Wi-Fi and volume. **USB debugging is not switched on, and adb is not a repair path on a
QR-provisioned tablet**: after the factory reset Developer options are off, and with Settings unreachable nobody
can turn them on. (Switching adb on from the app was considered and rejected: an authorisation prompt on a
public kiosk is a risk.) The ways out are the web page, the PIN, and the crash-loop breaker below.

It is applied when the display settings arrive (after Save on the display page), again after a
boot or an app update, and it is idempotent: applying it twice changes nothing.

## Turning the lockdown off

Always possible, in this order of preference:

1. **From the web page:** Displays > Edit > untick **Lock player** > Save. Within about a
   minute the tablet gives everything back: status bar, Settings, factory reset.
2. **Remote unlock** (display page, **Unlock remotely**, 5 minutes to 1 hour): lifts the
   lockdown for that time, then locks again. The tablet must be online.
3. **On the tablet:** tap the top-left corner 7 times within 3 seconds (or long-press Menu/OK on a
   remote), enter the unlock PIN. The Setup screen opens and the lockdown stays released for a
   **10 minute unlock window** (saved, so it survives an app restart; a reboot ends it, see Boot behaviour). Setup shows "Unlocked for N more
   minutes; the lockdown comes back after that" and a button **End the unlock now** (it returns to
   the player, which locks again). The lockdown never comes back while the window is open or while
   the Setup screen is in front, also when the tablet goes Home (Home is this app) in between. At the
   end of the window an alarm engages it again and brings the player back. Press **Remove Device
   Owner** on the setup screen to leave management for good.
4. **Automatic: crash-loop breaker.** If the app process starts 5 times within 10 minutes without a
   manifest having been fetched by a process that lived at least a minute (a broken update that crashes at
   start), the app releases the **whole** lockdown by itself (restrictions, persistent Home, status bar) and
   stays released until a manifest is fetched again. A tablet that is offline and rebooted five times in ten
   minutes therefore also comes back unlocked until the server is reachable.
5. **A release that fails is retried** every minute (an alarm, independent of the Lock player setting), at
   the next boot and at the next app start; release is never held back by a failed lock-down attempt.
6. **Last resort:** the hardware recovery menu (next section). It does not need the app.

## Android settings from the Setup screen

After the PIN unlock the Setup screen has one button per settings page: **Wi-Fi, Display, Date and
time, Sound, Apps, About the device**, and last the **main Settings page**. Each is opened on its
own; a page that cannot be opened falls back to the main page with a message. Device Owner only: a
**Factory reset this device** button (warning, then type `RESET`). It calls
`DevicePolicyManager.wipeData`, which works although the lockdown sets `DISALLOW_FACTORY_RESET`,
because that restriction only blocks the reset button in Settings, not the owner's own call. All data
is erased and the tablet must be provisioned again with a new QR code.

**What the first field log showed** (Samsung SM-X210, Android 16, QR-provisioned): after "unlock PIN
accepted" the main Settings page flashed white and closed at once, while the Wi-Fi page worked, and
2 to 5 seconds after every unlock "lockdown engage: done" appeared again. Two causes were assumed and
are both handled in the app: (1) Samsung's Settings main page may close itself when
`user_setup_complete` / `device_provisioned` are not set after QR provisioning (the log now prints
both at every app start and when Setup opens: look for `SetupFlags`); the direct pages above avoid
the main page. (2) the bounce landed on Home, which is this app, which restarted the player and
engaged the lockdown: the unlock window prevents that. Not confirmed on a tablet yet.

Marking set-up complete: there is no documented way for a Device Owner. The app tries the public
`DevicePolicyManager.setSecureSetting("user_setup_complete", "1")` once when Setup opens and the flag
is 0, and logs the result (`mark user set-up complete`). Android's allow-list for that call does not
document this key and probably refuses it (the log then says "refused"); hidden APIs and root are not
used. If it is refused the direct settings pages are the way, or a tablet provisioned through the
normal set-up wizard.

## Factory reset a locked tablet

1. Unlock it as above (web page, or PIN on the tablet). On the tablet the quickest way is Setup > **Factory reset this device** (type `RESET`).
2. Or on the tablet open **Settings > General management > Reset > Factory data reset**. (The
   status bar is back after the lockdown is released; if you cannot find Settings, use the
   app's setup screen after the PIN and press **Remove Device Owner**, then Home.)
3. If the tablet is not reachable at all (powered off, no PIN): hardware recovery. On most
   Galaxy tablets: power off, hold **Volume Up + Power** (some models Volume Up + Bixby/Power
   while plugged into USB), choose **Wipe data / factory reset**. The user restriction against
   factory reset blocks the Settings button, not the recovery menu. After a wipe the tablet asks
   for the previous Google account only if one was added (none was).

After a reset the tablet is back at the Welcome screen: scan a new QR code (a code is single
use; make another one).

## Commands from the web page

On the display page (Device agent switches must be on, and the app must be able to do it):

| Command | On Android |
|---|---|
| Reboot | Only as Device Owner. Acknowledged first, then `DevicePolicyManager.reboot`. At most once per 10 minutes on the device. |
| Update app | The app downloads the installer from the server with its display credential, checks the **signed** size and SHA-256 while saving it, then installs it with the system installer: **silent as Device Owner**, otherwise the system asks someone to confirm. It only installs a package with its own application ID signed by the same key as the installed app. The app restarts itself afterwards. |
| Startup | `autostart` on or off (enables or disables the boot receiver). `shell` mode is Windows only and is refused. The Device Owner Home role is separate: with the lockdown on the app is Home and starts anyway. |

Each command carries the server's signature; the tablet pins the server's command key the
first time it sees it (trust on first use, per server address). If the key later changes the
tablet refuses signed commands and reports `command_key_changed`; to accept the new key, use
**Forget server** in the app's setup (PIN protected) and pair again. Forget server also clears the
stored sequence number and server-time mark, so a restored or rebuilt server starts a fresh sequence. A tablet set
up by QR code gets the key inside the code and pins it before it ever contacts the server.

Install failures after the "installing" acknowledgement (signature mismatch, downgrade, refused prompt) are kept on
the tablet as `agent.last_update_error` and sent with the next heartbeat; the server keeps that field
(`player/diag.py`, at most 120 characters). The tablet's log has the detail.

## Samsung / Galaxy notes (honest about what is untested)

The QR route, the lockdown and the on-tablet unlock were run once on the SM-X210 above, which
is how the log findings below and the 3.0.1 to 3.0.3 changes came about. The rest follows
Android's documented provisioning rules and is unconfirmed. Expect to adjust the first time on
any other model.

- **6 taps on the Welcome screen** is the standard Android Enterprise gesture. On some Samsung
  builds the tap area or the number of taps differs; some carrier-branded tablets disable it.
  Samsung's own enrolment (Knox Mobile Enrollment) is a different route and is not used here.
- **Knox** may add its own screens (terms, "Samsung account", Knox agreement) during
  provisioning, or ask to apply policies. Accept the managed-device terms; do not sign in to a
  Samsung or Google account.
- Samsung's "Setup wizard" can keep the Home button, edge panels or the navigation gestures
  after provisioning. If a gesture still escapes the kiosk, report the model and One UI version.
- Wi-Fi: if the tablet has no network at the Welcome screen, add the Wi-Fi name and password
  when making the QR code, or connect manually when the scanner asks.
- Disabling the keyguard only works while no PIN or password lock screen is set on the tablet.
- Android 12 and newer ask the app how to provision (the app answers "fully managed device").
  Android 9 to 11 use the older path. Both are implemented; only the documented path is verified.

## How this relates to the adb method

[kiosk-lockdown.md](kiosk-lockdown.md) makes the app Device Owner with
`adb shell dpm set-device-owner ...` on a fresh tablet. The lockdown then behaves exactly as
described above, because it is driven by the same **Lock player** switch. The QR route adds the
automatic download, install, pairing and the first start.

## Manual test checklist

Run on a spare tablet; tick each.

1. [ ] QR page: code made; on the tablet 6 taps open the scanner; scan; the app installs.
2. [ ] The tablet shows "Setting up this screen", then the playlist, with no touch.
3. [ ] The screen appears in Displays, paired, with the name you chose.
4. [ ] Diagnostics show tier `device_owner`, can reboot, can update, startup autostart on.
5. [ ] Scan with the server switched off (or Wi-Fi off right after the install): "Setting up this
       screen" keeps retrying, and finishes by itself when the server is back (within 10 minutes).
6. [ ] Lock player on (with a PIN): status bar gone, Home does nothing, Settings unreachable,
       the tablet does not sleep on the charger.
7. [ ] Reboot the tablet: the player is back with the lockdown on, with no touch.
8. [ ] Lock player off: status bar and Settings are back within about a minute; screen sleep is
       as before.
9. [ ] Lock player on again, then **Unlock remotely** 5 minutes: unlocked, then locked again.
10. [ ] PIN exit on the tablet leaves the lockdown; **Remove Device Owner** works.
11. [ ] Send **Reboot** from the web page: acknowledged "rebooting", the tablet restarts; a
        second reboot within 10 minutes is refused.
12. [ ] Send **Update app** with a newer build: acknowledged "installing x.y.z", installs
        silently, the app comes back; a package that was changed after signing is refused.
13. [ ] Set autostart off, reboot: the app does not start by itself unless the lockdown (Home) is on.
14. [ ] Rotate the server's command key (delete `command_key` on the server): Tier B commands are
        refused and the display page shows the key change; **Forget server** and re-pair fixes it.
15. [ ] With a wrong address in the QR code, tap the setup text 7 times: the confirmation opens, then setup. Also wait 10 minutes: same result.
16. [ ] Crash loop: install a build that crashes at start (or force-stop the app 5 times in 10 minutes while offline): the lockdown is released by itself; fetch a manifest and it locks again.
17. [ ] If a release ever fails on a real device, the log shows "lockdown release: failed steps" and a retry a minute later (cannot be forced; read the log after a test).
18. [ ] Factory reset the locked tablet using the steps above, then repeat from step 1.

## Boot behaviour (what the first tablet showed)

- A **reboot ends the on-device unlock window**: the tablet always comes back locked (if Lock player is on) and goes
  straight to the player. In 3.0.2 the window survived a reboot, and the tablet booted unlocked to the Samsung home
  screen before the player started.
- With **Use as Home app** ticked (the default for a provisioned tablet) the player stays the Home app even after the
  lockdown is lifted, so the tablet always boots into AiSignX. Untick it on the Setup screen to get the tablet's own
  launcher back.
- Android finishes its own first-time setup (`user_setup_complete`) only at the first reboot after QR provisioning. Until
  then Samsung's main Settings page closes itself (the log showed `user_setup_complete=0`, then `1` after the reboot).
  Reboot the tablet once after provisioning and the main Settings page works.
