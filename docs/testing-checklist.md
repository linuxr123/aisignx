# AiSignX v3: manual test checklist

Everything here needs a human, a real screen, or real hardware. Automated tests (about 1,300 on the server, plus the Windows and Android app suites) already
pass; this list covers what they cannot. None of the device sections have been run on more than the owner's Galaxy Tab and a Shield. Tick as you go; write what you saw next to anything that fails.

**Before you start:** restart the server (`flask --app wsgi serve`), open the admin in a browser, sign in as admin.
Restart the server after any server code change before testing it. Have ready: one Windows PC with the player app, the Android phone, tablet and/or Shield, a PowerPoint PC, and 2+
screens (or browser tabs at `/player`) for sync tests.

---

## A. Server installers (use a spare machine or VM, not your main server; Windows is the main target)

| # | Step | Expect | Result |
|---|---|---|---|
| A1 | Windows: `powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1 -DryRun -Yes` | Lists every step, changes nothing | |
| A2 | Windows: run it for real (accept the admin prompt) | Ends with "AiSignX is installed", prints an address | |
| A3 | Open that address from another PC on the LAN | Setup wizard (create admin) appears | |
| A4 | Reboot the PC, do not log in, wait 2 min, open the address | Server is up (starts with the computer) | |
| A5 | Task Scheduler: end the "AiSignX Server" task's process | It comes back within ~1 min | |
| A6 | Run the installer again (upgrade) | "Backing up the database", server back up, data still there, `app.prev` exists | |
| A7 | `-Uninstall`, then check `C:\ProgramData\AiSignX` | Service gone, data kept | |
| A8 | Linux (best effort, never run): `sudo ./scripts/install-server.sh`, then `systemctl status aisignx`, reboot | Running, survives reboot | |
| A9 | Linux: `sudo ./scripts/install-server.sh --uninstall` | Service removed, data kept | |

macOS is dropped: the install script still has a launchd branch, but it is not supported and not tested.

## B. Downloads and updates

| # | Step | Expect | Result |
|---|---|---|---|
| B1 | Sidebar > Downloads (also in the user menu), as admin | Page opens with an "Add a file" form | |
| B2 | Upload the Android APK, kind "Android player app" | Listed with size and SHA-256 | |
| B3 | On the phone's browser: sign in, open `http://<server>:5000/downloads/`, tap the APK | Downloads; Android asks to allow installs from the browser, then installs | |
| B4 | Compare the shown SHA-256 with `certutil -hashfile <file> SHA256` on the PC | Same | |
| B5 | Sign in as a viewer: Downloads page | Can download, no upload/remove buttons | |
| B6 | Signed out: open a download link | Redirected to sign in | |
| B7 | Admin > Updates: "Check now" | Shows "Up to date" or "no releases published yet" (no error page) | |
| B8 | Turn the check off, then on | Button label changes, setting sticks | |

## C. Device agent (real devices)

For each: Displays > the display > Edit > "Device agent": tick **Restart watchdog**, **Allow restart**, **Allow
log collection**, save. (Install the newest app build first.)

**Windows app**

| # | Step | Expect | Result |
|---|---|---|---|
| C1 | Display page shows the agent card with the app version | Card present, capabilities listed | |
| C2 | Click **Restart player** | Player closes and reopens within ~1 min; command shows "done" | |
| C3 | Kill the player in Task Manager | It is started again within ~1-2 min | |
| C4 | Click **Collect logs**, wait, then Download | A .gz file downloads; open it: no PIN, tokens or cookies in it | |
| C5 | Untick "Allow restart", click Restart | Refused with a clear message | |

**Android (phone and Shield)**

| # | Step | Expect | Result |
|---|---|---|---|
| C6 | Restart player | App restarts and shows the player again | |
| C7 | Collect logs and download | File arrives, no secrets in it | |
| C8 | Freeze the page if you can (airplane mode is not a freeze; skip if you cannot) | n/a, note if the app ever stays stuck | |
| C9 | Shield only: leave playing 1 hour | Still playing; Displays > diagnostics shows no "gave up" | |

## D. Synchronized playback

Make a group with 2+ displays, tick **Synchronized playback**. Put 3 slides in its playlist: an image, a
video, a YouTube clip.

| # | Step | Expect | Result |
|---|---|---|---|
| D1 | Start all screens | Same slide at the same moment (within a fraction of a second) | |
| D2 | **YouTube clip** with start and end set (for example `VIDEOID 0:10 0:40`) | Plays once from the start time to the end time, then the slide ends and the next one shows. It must NOT loop over and over. | |
| D3 | YouTube with only a start time | Plays from the start; the slide lasts the item's duration (set it to the length you want) | |
| D4 | RSS news slide, list mode, on screens with **different sizes or orientations** (landscape and portrait) | Pages change at the same moment; landscape screens all show the same 3 headlines, portrait screens the same 4 | |
| D5 | RSS: wait until the feed refreshes (or change it) | New headlines appear on all screens at the next page change, not at random moments | |
| D6 | Pull the network cable from one screen for 1 minute, put it back | It rejoins in sync | |
| D7 | Web player, Windows app and Android in the same group | All three show the same slide | |

## E. Plugins and playback

| # | Step | Expect | Result |
|---|---|---|---|
| E1 | Stock ticker: enter the Finnhub key on the plugin's page | Saved; quotes show | |
| E2 | YouTube: captions off | No captions visible. Captions on: captions visible | |
| E3 | YouTube: mute and volume from the playlist | Applied on the screen | |
| E4 | Go offline (unplug) with news, stocks and YouTube slides | Screen shows the offline notice or the last data, then moves on; it does not freeze | |
| E5 | Android Shield: the big MP4 with fast-forward and rewind | Smooth, no freezes | |
| E6 | A web page item | Shows the page (or a screenshot), no error | |
| E7 | Restart the app on the Galaxy and the Shield (so nothing is cached), then play the biggest MP4 in a synced playlist. Watch the FIRST showing, and note how it looks | Plays without freezing and without a green block. If it still freezes, it should recover within a few seconds (look in the display's diagnostics/errors for "video not advancing, nudging play()" or "sync seek waits for the video to load"). Tell me what you saw | |

## F. Content and server basics

| # | Step | Expect | Result |
|---|---|---|---|
| F1 | Upload an image, a video, add a web URL | Thumbnails and durations appear | |
| F2 | Media > "Fix this file" on a video flagged as hard to play | Converted copy plays on the weak device | |
| F3 | Pair a new display with the short code | Appears online in the list | |
| F4 | Unplug a display | Goes offline in ~minutes and an alert is raised | |
| F5 | Emergency message | Shows on all screens at once and clears when ended | |
| F6 | Backups > create, then restore on a spare instance | Data comes back | |

## G. PowerPoint add-in (from the installer zip)

| # | Step | Expect | Result |
|---|---|---|---|
| G1 | On another PC, unzip and run the launcher; trust the certificate | Installs, ribbon button appears in PowerPoint | |
| G2 | Plugins window: enter server and token, **Test** | Clear success or failure message | |
| G3 | Upload a slide deck as images | Appears under Media | |
| G4 | Open the deck from a network drive as another user | Works without changes (their own token) | |

## H. Emergency: saved messages, schedule and API

| # | Step | Expect | Result |
|---|---|---|---|
| H1 | Emergency > fill the form, name it, **Save as template** | Saved; nothing is sent; no "send now?" prompt | |
| H2 | **Send now** on the saved message | Asks to confirm, then every chosen screen shows it within seconds | |
| H3 | Clear it | Screens return to normal content | |
| H4 | Scheduled: pick the saved message, start 2 minutes from now, stays up 3 minutes | Appears on screens at the start time (within ~30 s), disappears 3 minutes later; history shows "expired" | |
| H5 | Schedule one, then Cancel it before it starts | Nothing is sent | |
| H6 | Stop the server for 15 min across a scheduled start, then start it | Plan shows **missed**, nothing is sent late | |
| H7 | Delete the group a saved message targets, then Send now | Refused with a message; it does NOT go to all screens | |
| H8 | API: make a token with **emergency:send** (user menu > My API tokens); `curl -X POST -H "Authorization: Bearer TOKEN" http://SERVER:5000/api/v1/emergency/templates/ID/send` (ID is shown on the Emergency page) | 201; screens show it. Run it twice: second answer says already live, no duplicate | |
| H9 | `curl -X POST -H "Authorization: Bearer TOKEN" http://SERVER:5000/api/v1/emergency/clear` | Screens clear | |
| H10 | A viewer-role user: try to create a token with emergency:send | Not offered | |

## I. Update notice

| # | Step | Expect | Result |
|---|---|---|---|
| I1 | Upload an APK (or Windows installer) to Downloads with a version newer than the one on a device | That display's page shows "Update available: <version>" next to its app | |
| I2 | Same version as the device | No notice | |

## J. Galaxy tablet as a locked kiosk (QR provisioning) - see docs/android-kiosk.md

Use a tablet that holds nothing you need. First upload the newest Android APK on Downloads.

| # | Step | Expect | Result |
|---|---|---|---|
| J1 | Downloads > **Set up an Android tablet as a kiosk**; name it, tick lock and manage, add your Wi-Fi, **Make the QR code** | A QR code appears; the page names the app and version it will install | |
| J2 | Factory reset the tablet. On the Welcome screen tap the same spot 6 times, scan the code | The tablet downloads the app from your server (no Google account needed), installs it, and shows "Setting up this screen" | |
| J3 | Wait | The tablet appears in Displays already paired, and plays its playlist with no one touching it | |
| J4 | With Lock player on: try Home, Back, Recents, pulling down the status bar, power menu | None leave the app; the screen stays on while charging | |
| J5 | On the display page, press **Reboot the device** | It reboots, comes back, and goes straight back to the player | |
| J6 | Upload a newer APK (higher version) to Downloads, open the display page, press **Push the update** | The tablet installs it silently and returns to the player | |
| J7 | Lock the tablet, do the 7-tap exit and enter the PIN | Setup opens and shows "Unlocked for N more minutes"; the lockdown does NOT come back (no "lockdown engage" in the log) for 10 minutes, also not after Home or after opening Wi-Fi/Display/Date/Sound/Apps/About pages; the log has a `user_setup_complete=... device_provisioned=...` line. Press "End the unlock now": the player returns and locks. After 10 minutes the lockdown comes back by itself. Also check which settings pages open (the main page may still close by itself) | |
| J8 | Setup > **Factory reset this device** (Device Owner only): Cancel, wrong word, then RESET; or alternatively Lock player off and Settings > Factory data reset | Cancel and a wrong word erase nothing; RESET erases the tablet, which then shows the Welcome screen and can be provisioned again from J2 | |
| J9 | Try Reboot / Push the update with those switches off (Edit > Device agent) | Refused with a message | |
| J10 | With the lockdown on, hold Power + Volume Down and choose Power off | The power menu opens even in the lockdown and the tablet shuts down. There is no remote shutdown; the admin can only reboot (J5). Write down if your model behaves differently | |
| J11 | Look for a brightness setting on the display's edit page and in the Device agent options | There is none: a brightness setting is not built (it is on the planned list). Set the brightness on the tablet itself | |

## K. Windows: reboot, startup and updates (signed commands)

Use a spare Windows PC with the installed app (not `npm start`). Edit the display > Device agent: switch on reboot, startup and update.

| # | Step | Expect | Result |
|---|---|---|---|
| K1 | Display page > Device agent | Shows the agent version and the new abilities; the buttons are enabled | |
| K2 | **Reboot the device** | The PC restarts about 30 seconds later; the display page shows the command done | |
| K3 | Startup form: Start the player at sign-in = On | After the next sign-in the player starts by itself | |
| K4 | Startup form: Run as = the Windows shell, then sign out and in | The player replaces the desktop; there is no taskbar. If the player is closed, the desktop appears again (fallback) | |
| K5 | Startup form: Run as = An app | The normal desktop returns after the next sign-in | |
| K6 | Upload a newer installer, **Push the update** | The app installs it and comes back; in shell mode the Explorer desktop does not appear | |
| K7 | Press Reboot twice within 10 minutes | The second is refused | |

## L. Windows: privileged agent (docs/windows-agent.md)

A spare Windows PC with the installed player, a standard (non-admin) Windows account for the screen, and the signing certificate of the build PC. Build the kit on the build PC first (`npm run dist:win`, then `scripts\Build-Agent.ps1`).

| # | Step | Expect | Result |
|---|---|---|---|
| L1 | Display page > Privileged agent > **Enroll the privileged agent** (as an admin) | A one-time code and a ready command line, shown once | |
| L2 | On the PC, PowerShell **as administrator**: run the command line from the page with `-KioskUser <the screen account> -AllowReboot -AllowStartup -AllowUpdate` | The script prints the plan and finishes with "The agent is running" | |
| L3 | Run the same script in a normal (not elevated) PowerShell | It refuses: "Run this script elevated" | |
| L4 | Run it with a wrong `-KeyFingerprint` | Enrollment stops with "does not match the fingerprint"; no task is created | |
| L5 | Display page | "Privileged agent" card shows enrolled, last seen under a minute, the abilities, and the player's own Tier B buttons now talk to the agent | |
| L6 | Task Scheduler (or `Get-ScheduledTask 'AiSignX Agent'`) | Runs as SYSTEM, starts at boot, restarts after `taskkill /F /IM aisignx-agent.exe` within a minute | |
| L7 | As the standard user: try to change, rename or delete anything in `C:\ProgramData\AiSignXAgent` and `C:\Program Files\AiSignX\Agent`; try to plant a junction there | Access denied everywhere except reading `public\status.json`; the agent keeps working | |
| L8 | Press **Reboot the device** | The PC restarts about 30 seconds later; the command shows done | |
| L9 | Startup form: shell mode, then back to app mode | Works with nobody logged in (the agent edits the kiosk user's profile) and is undone cleanly | |
| L10 | Sign the installer with the pinned certificate (`scripts\Sign-Installer.ps1`), upload it, **Push the update** | The agent installs it silently, the player comes back on the new version, the command shows done, the card shows last update "ok" | |
| L11 | Upload the same installer **unsigned** and push the update | Refused: command failed with "bad_signature"; the running player is untouched; nothing left in `ProgramData\AiSignXAgent\staging` | |
| L12 | Sign with a **different** self-signed certificate and push | Refused with "wrong_signer" | |
| L13 | Change one byte of a signed installer, upload, push | The server's hash is of the changed file, so Windows reports a broken signature: refused ("bad_signature") | |
| L14 | Push an update that does not start (a deliberately broken build signed with the right key) | After 10 minutes the agent reinstalls the previous version once; the card shows "rolled back" | |
| L15 | With the agent running, try the in-app path: stop the agent task and restart the player | Within 15 minutes of the agent stopping, the player's own reboot/update/startup handling is live again (until the agent returns) | |
| L16 | Display page > Privileged agent > **Turn on automatic sign-in** (over https): kiosk account name and password, then your own AiSignX password | Accepted; the command history shows no password; after delivery the database row has no ciphertext | |
| L17 | Reboot the PC | Windows signs the kiosk account in by itself | |
| L18 | `reg query "HKLM\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon" /v DefaultPassword` | Not found (the password is an LSA secret, not a registry value) | |
| L19 | Try the form over plain http from another PC; try as an editor; try another account name | Refused each time | |
| L20 | **Turn it off** | Windows asks for the password at the next boot; the LSA secret is gone | |
| L21 | `Install-Agent.ps1 -Uninstall -Yes`, then press **Remove agent** on the display page | Task, folders, certificate trust and the auto-login/startup settings are gone; the player works as before | |
| L22 | Run the script against `http://` and again against `https://` | A warning for http; the auto-login form is refused over http | |
| L23 | Run the script with `-DryRun` first | Checks every parameter and prints the plan; changes nothing | |

## M. Android TV box or phone: install link (docs/android-tv.md)

Only the NVIDIA Shield has been tried. Upload the newest Android APK on Downloads first.

| # | Step | Expect | Result |
|---|---|---|---|
| M1 | Downloads > **Set up an Android tablet as a kiosk**, scroll to **Phones that are already set up, and Android TV boxes**, press **Make an install link** | A short address such as `192.168.1.19:5000/get/K7M2Q9XD` and a QR code; a new link replaces the old one; valid 7 days | |
| M2 | Phone: scan the QR code, **Download the app**, allow installs from the browser, install | The app installs and opens its setup screen | |
| M3 | TV box: install Downloader (AFTVnews) from the box's store, type the full address with `http://`, open the page, **Download the app**, Install | The app installs after you allow Downloader to install apps | |
| M4 | In the app: server address, **Test**, **Save and start**, then pair the code under Displays > Pair a display | The screen plays its playlist | |
| M5 | Open the install link with a wrong or expired code, or while signed out | A 404 page for a wrong or expired code; no login needed for a valid one | |
| M6 | Chromecast with Google TV, Fire TV (if you have one) | Same route as M3; note what differs | |

---

**Report back with:** the row numbers that failed and what you saw (a screenshot helps). Logs: Admin > Logs, and
for apps use Device agent > Collect logs.
