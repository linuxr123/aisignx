# AiSignX v3: manual test checklist

Everything here needs a human, a real screen, or real hardware. Automated tests (about 1,000) already
pass; this list covers what they cannot. Tick as you go; write what you saw next to anything that fails.

**Before you start:** restart the server (`flask --app wsgi serve`), open the admin in a browser, sign in as admin.
Have ready: one Windows PC with the player app, the Android phone and/or Shield, a PowerPoint PC, and 2+
screens (or browser tabs at `/player`) for sync tests.

---

## A. Server installers (use a spare machine or VM, not your main server)

| # | Step | Expect | Result |
|---|---|---|---|
| A1 | Windows: `powershell -ExecutionPolicy Bypass -File scripts\install-server.ps1 -DryRun -Yes` | Lists every step, changes nothing | |
| A2 | Windows: run it for real (accept the admin prompt) | Ends with "AiSignX is installed", prints an address | |
| A3 | Open that address from another PC on the LAN | Setup wizard (create admin) appears | |
| A4 | Reboot the PC, do not log in, wait 2 min, open the address | Server is up (starts with the computer) | |
| A5 | Task Scheduler: end the "AiSignX Server" task's process | It comes back within ~1 min | |
| A6 | Run the installer again (upgrade) | "Backing up the database", server back up, data still there, `app.prev` exists | |
| A7 | `-Uninstall`, then check `C:\ProgramData\AiSignX` | Service gone, data kept | |
| A8 | Linux: `sudo ./scripts/install-server.sh`, then `systemctl status aisignx`, reboot | Running, survives reboot | |
| A9 | macOS: `sudo ./scripts/install-server.sh`, reboot | Running after reboot | |
| A10 | Linux/macOS: `--uninstall` | Service removed, data kept | |

## B. Downloads and updates

| # | Step | Expect | Result |
|---|---|---|---|
| B1 | User menu > Downloads (as admin) | Page opens with an "Add a file" form | |
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

---

**Report back with:** the row numbers that failed and what you saw (a screenshot helps). Logs: Admin > Logs, and
for apps use Device agent > Collect logs.
