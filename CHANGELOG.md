# Changelog

## Project status (updated 2026-11-17)

A quick overview. The detailed list of every change follows, newest first. Everything is listed
as "Unreleased": no release has been published yet.

**Honest limits:** the automated tests pass (about 1,300 on the server, plus the Electron, Android and PowerPoint
suites), but almost nothing has run on real hardware. Only the owner's Galaxy Tab and an NVIDIA Shield have been
tried. The Windows privileged agent, the server installers (the Scheduled Task and systemd parts), signed
commands on Windows and the PowerPoint add-in inside PowerPoint have never run on a real machine. See
[docs/testing-checklist.md](docs/testing-checklist.md).

**Platforms:** Windows and Android are primary. Linux is best effort, with free means only. macOS is dropped.

### Done
- **Core:** setup wizard (no default passwords), sign-in, users and roles (admin, editor, viewer), settings.
- **Content:** media library (images, videos, web pages with screenshot previews), playlists with drag-and-drop, durations, transitions and fit modes.
- **Screens:** pairing with a short code, browser player at `/player/`, live updates within about a second, online/offline status, synchronized playback for a group.
- **Scheduling:** schedules by day, time and date, display groups, emergency messages with saved templates, scheduled emergencies and an API.
- **Player apps:** Windows (Electron, current installer 3.1.0) and Android 3.0.3 (phones, tablets and TVs), with offline caching and certificate pinning.
- **Display options:** orientation, volume, offline banner, location, exit PIN, Identify, remote screenshots, diagnostics.
- **Player controls:** pause, next/previous and seek by keyboard, mouse, touch and TV remote. **Lock player** kiosk mode with remote unlock.
- **Device management:** the device agent (watchdog, restart player, collect logs), signed commands (reboot, startup, app update), the optional SYSTEM-level Windows agent with automatic Windows sign-in, Android QR provisioning with Device Owner lockdown.
- **Distribution:** server installers (Windows, Linux), the Downloads page with automatic kind and version detection, the install link for phones and TV boxes, an update notice for the apps.
- **Operations:** backup and restore, audit log, alerts by e-mail, proof of play, API tokens and a media upload API, health checks and logs, Docker, HTTPS guide, update check and release signing.
- **Plugins:** the plugin framework and seven plugins: clock, weather, weather radar, RSS / Atom, stocks, YouTube and image library.
- **PowerPoint add-in:** built and documented (3.0.2); needs a hands-on test in PowerPoint (checklist in its README).

### In progress
- Hands-on testing on real devices: plugins in the Windows and Android apps, the Windows privileged agent, the tablet kiosk on more than one model (see "Not yet checked" and the test checklist).

### Planned (not built)
1. **One-click server update:** a button on Admin > Updates that downloads a release, verifies its signature (public key built into the server), backs up the database, upgrades and restarts. Needs a privileged helper and a generated release key; works only for servers set up by the installer scripts. Today the page only shows "update available"; see [docs/updates.md](docs/updates.md).
2. **Recurring emergencies** (planned emergencies repeat on a schedule; today a plan runs once).
3. **API plan scheduling:** planning an emergency through the API (today the API sends and clears prepared messages only).
4. **Brightness setting** for screens (no setting exists, on any platform).
5. **Windows live mode for web pages:** an optional live mode in the Windows app for sites that forbid embedding (screenshots already cover them).
6. **Content extras:** media tags and folders, smart playlists (filled by tags), a schedule conflict view.
7. **Later, maybe:** multiple sites and single sign-on (Phase 7, on hold).

Done since the last plan: the device agent and watchdog, signed commands, the Windows privileged agent, the update check, release signing, saved and scheduled emergencies, the emergency API, the Downloads page and the install link.

### Fixed 2026-11-14
- Videos whose MP4 index is at the end of the file (e.g. "sample-10s") stalled on Android and Windows. Uploads are now rewritten with the index at the front (lossless), and the existing file was fixed.

## Unreleased: Android TV install from a PC

### Added
- **PC install script for Android TV boxes:** the install-link card now offers "Download the PC script" (`/get/<code>/tv.ps1`). It finds or downloads Google's `adb`, downloads the app from your server and checks its SHA-256, connects to the box over the network, installs the app and opens it with the server address filled in (optional `-DeviceOwner`). No password or pairing secret is inside the script. Replaces typing an address with a remote and the free Downloader app as the recommended route (`docs/android-tv.md`).
- **Android 3.0.5:** the setup screen accepts a server address from the launch intent (first set-up only; it fills and tests the box, Save stays a person's choice).

## Unreleased: Android 3.0.4, run modes on Android

### Changed
- **Android 3.0.4:** the three "How the player runs" choices from the display page now work on Android too. **Shell** means the app is the **Home app** (the device boots into it and the Home button returns to it), **Application** turns Home mode off, **Application with auto-start** starts it at boot. With Lock player on the app is always the Home app, so switching to Application is refused with a clear message. The Setup screen's "Use as Home app" tick and the command now share one code path. Not verified on a device.

## Unreleased: Emergency page redesign

### Changed
- **The Emergency page is rebuilt:** a status line ("All clear" or "N live now") and a large banner per live message with a big **Clear it** button; your saved messages as one-click cards colour-coded by level (with the screens they go to and the API id); a compact **Scheduled** panel (upcoming sends, a small form, earlier plans folded away); a **New message** composer with level buttons and a **live preview** of how it will look on a screen; and a tidier history with **Send again**. Same features and routes as before.

## Unreleased: One "Run as" choice for every screen

### Changed
- **Display page: "How the player runs"** is now one clear choice, the same on Windows and Android: **Application** (starts when someone opens it), **Application with auto-start** (starts by itself at sign-in or boot), or **Shell** (takes over the device: on Windows the player replaces the desktop for the kiosk user, with a fallback to the normal desktop; on Android it is the Home app). It shows the current mode and greys out what a screen cannot do. It sends the same signed `set_startup` command as before.

## Unreleased: Backups carry the command key

### Fixed
- **Backups now include `instance/command_key`** and a restore brings it back. Before, restoring onto a new machine made a new key and screens that had pinned the old one refused reboot, startup and update commands. The `downloads` folder is still not in a backup: upload the installers again.

## Unreleased: Android kiosk, install link and app versions 3.0.0 to 3.0.3

### Added
- **Android 3.0.0:** the app carries out signed commands (reboot, startup, app update, see "Signed device commands"), is set up by **QR provisioning** and, as Device Owner, runs the **kiosk lockdown** driven by the display's **Lock player** switch (lock task mode, no Home, Recents or notifications, screen stays on, release after a crash loop or an unreachable server). The QR code carries the app address, a checksum, the server's command key (pre-pinned) and optional Wi-Fi details. See [docs/android-kiosk.md](docs/android-kiosk.md) and [docs/kiosk-lockdown.md](docs/kiosk-lockdown.md).
- **Install link** (Downloads > "Set up an Android tablet as a kiosk" > "Phones that are already set up, and Android TV boxes"): a short public address `/get/<code>` and a QR code that open a page with the app and the server address, for phones and Android TV boxes that are not wiped (the Shield route, with the Downloader app on a TV). The link is valid for 7 days and a new link replaces the old one. See [docs/android-tv.md](docs/android-tv.md).
- The Android setup screen shows **Android settings** and **Wi-Fi** buttons after the unlock.

### Fixed
- **Provisioning loop:** the tablet read its display cookie at the site root, never saw it, and looped on "waiting for the network and server". It now reads it at its own path (`/api/player`).
- **Provisioning QR code:** a fixed size that overflowed its box (half white, half black on a dark page); it now has a solid white background, scales to fit and uses a shorter link.
- **3.0.1:** version bump (versionCode 301) with the fixes above.
- **3.0.2:** a local **unlock window** after the PIN (the lockdown does not come back while you use Android's settings), direct links to the settings pages, an on-device **factory reset** (Device Owner only, confirmed with a typed word) and logging of the set-up state.
- **3.0.3:** a **reboot ends the unlock window** (in 3.0.2 it survived a reboot and the tablet booted unlocked to the Samsung home screen); **Use as Home app** keeps the player as Home after the lockdown is lifted, so the tablet boots straight into it.
- Only one tablet (a Samsung SM-X210) has run this; see the Galaxy notes in [docs/android-kiosk.md](docs/android-kiosk.md). On that tablet, Power + Volume Down still opens the power menu in the lockdown and shutting down from it works; there is no remote shutdown.

## Unreleased: Windows privileged agent

### Added
- **A small SYSTEM agent for Windows PCs** ([docs/windows-agent.md](docs/windows-agent.md), design in [docs/windows-agent-service.md](docs/windows-agent-service.md)): reboot, startup/shell mode, app updates and (new) automatic Windows sign-in, done by a program that only the administrator can install or change, and that obeys only commands signed by the server's pinned key. An update is installed only if the server-signed command (display, expiry, seq, size, sha256) **and** the installer's Authenticode signature (the pinned certificate thumbprint) both check out on the same bytes, from a protected folder. It rolls back once to the previous verified installer if the new version does not come up.
- **Enrollment:** the display page makes a one-time code (admin only, 15 minutes, hashed); the elevated `Install-Agent.ps1` enrolls with it and compares the server's command key with the fingerprint you typed. The agent has its own token that works only on `/api/player/v1/agent/*`; once it is enrolled the player's token no longer gets Tier B commands.
- **Automatic Windows sign-in, remotely:** the password is encrypted in the admin's request to the PC's own X25519 key (HKDF-SHA256, AES-256-GCM), the server stores only the ciphertext and wipes it when the command is delivered, and it is never logged or audited. The agent keeps it as an LSA secret, not a registry value, and only for the one account chosen at install.
- `scripts\Build-Agent.ps1`, `Sign-File.ps1`, `Sign-Installer.ps1` and an electron-builder signing hook (thumbprint from `AISIGNX_SIGN_THUMBPRINT`); the installer becomes the assisted NSIS form so one signed file serves per-user and all-users installs.
- The player's own Tier B code (reboot, startup, update) stands down while the agent is in charge and remains the fallback when it is not installed.
- Migration 0020; tests on both sides, including Python-signed commands and a Python-encrypted password opened by the node agent.
- Not verified on real hardware yet (see docs/testing-checklist.md section L).

## Unreleased: Signed device commands (server side)

### Added
- **Reboot, startup settings and app updates for screens** as **signed** commands ([docs/signed-commands.md](docs/signed-commands.md)): the server signs each with its own Ed25519 command key; the apps pin the key on first contact and verify every command (display, expiry on server time, replay counter, typed parameters) before acting. Per-display switches under Edit > Device agent. Reboot is limited to once per 10 minutes. Migration 0018.
- **Display page:** Reboot button, a "Start the player at sign-in" / "Run as an app or the Windows shell" form, and **Push the update** when the installer on the Downloads page is newer than the screen's app. The installer is chosen by the server (not by the request) and is served only while that screen's own update command is open.
- **Displays list:** tells admins when screens run an older app than the Downloads page, and when a screen already runs a newer version than the page has ("upload that installer to update the others").
- Admin > System shows the command key fingerprint.
- **Android tablet provisioning (server side):** Downloads > "Set up an Android tablet as a kiosk" makes a QR code for a factory-reset device: it downloads the app from this server (time-limited link, checked against a checksum in the code), is made Device Owner, and pairs itself with a one-time secret. The kiosk lockdown is then controlled by the display's Lock player switch. Wi-Fi details can be included.
- The Windows and Android apps carry out these commands (added with the client work, see the Android and Windows agent entries). Until an app reports the abilities, the buttons stay disabled. Unit tests only; no real Windows screen has run them.

## Unreleased: Sync polish and video watchdog

### Fixed
- **Big video freezing (and a green block on a Galaxy) on its first showing:** the player's frozen-video watchdog used to treat a paused or still-seeking video as healthy forever. It now asks a paused or stationary video to play (after 2 to 3 s, repeatedly), asks again if a seek takes over 6 s, and gives up on the slide after 10 s (20 s for a seek); it never interferes when the viewer paused on purpose. A slide that starts late no longer seeks a video that is still loading: it starts from the beginning and jumps once the video can play, and drift corrections skip paused videos and small corrections into data that has not arrived. Only the first showing was affected (the file is not cached on the device yet, so it streams while it downloads). Found by a read-only investigation, not reproduced on a device: please check the new rows in the test checklist.
- **Synced video freezing until the next slide (first showing):** a video that is still loading is no longer seeked by the drift correction (which restarted its download), corrections are limited to one per 10 s, and a video that stops advancing for 3 s gets a play() nudge before the 10 s stall rule gives up on it. Not reproduced here; please watch for it again.
- **News page order on synced screens:** list pages now count from the start of the slide, so every screen starts on page 1 and goes 1, 2, 3 (before it followed the wall clock: 2, 3, 1). Plugin frames get the slide's start time (`ctx.slideStart`).

## Unreleased: Emergency templates and API

### Added
- **Saved emergency messages:** save the send form as a named template; send it in one click from the Emergency page. A template whose group or display was deleted refuses to send rather than widening to all screens. Migration 0016.
- **Scheduled emergencies:** pick a saved message, a start time and an optional "stays up for" time; the server sends it, then clears it ("expired"). A plan more than 10 minutes late is marked missed and not sent. Migration 0017.
- **Emergency API:** new token permission `emergency:send` (editors and admins). `POST /api/v1/emergency/templates/<id>/send`, `POST /api/v1/emergency/clear`, `GET /api/v1/emergency` and `/templates`, for buttons, scripts and webhooks. Prepared messages only, no duplicate stacking, audited. See [docs/emergency-api.md](docs/emergency-api.md).

## Unreleased: Sync fixes, test checklist

### Fixed
- **News (RSS) not identical across synced screens:** list pages now hold a fixed number of rows per orientation (3 landscape, 4 portrait) instead of being cut from each screen's own text measurements, which differed between screens. A changed feed is applied at the next shared page change, not at random moments.
- **YouTube clip looping in a synced group:** a single video with a start and an end time now gets a slide exactly that long in synced groups, so it plays once and the playlist moves on. (With only a start time the slide still lasts the item's duration.)

### Added
- **App update notice:** a display's page shows "Update available" when its Android or Windows app is older than the newest installer on the Downloads page (versions compared properly, 3.0.10 > 3.0.2). It informs, and with signed commands an admin can now push the update (see "Signed device commands" and [docs/updates.md](docs/updates.md)).
- `flask --app wsgi downloads add FILE --kind ...` to publish installers to the Downloads page; [docs/downloads.md](docs/downloads.md).
- [docs/testing-checklist.md](docs/testing-checklist.md): the manual tests, step by step.

## Unreleased: Update check

### Added
- **Update check** (Admin > Updates): a daily anonymous look at the GitHub releases page; admins see a banner and the release notes when a newer version exists. Can be switched off (setting or `AISIGNX_UPDATE_CHECK=0`).
- **Release signing:** Ed25519 verification built into the server and `tools/release-sign.py` (keygen, sign, verify). See [docs/updates.md](docs/updates.md).
- Not yet: the one-click download-and-install button (needs a privileged helper, a generated release key and a published signed release). No signing key exists yet.

## Unreleased: Downloads area

### Added
- **Downloads page** (sidebar and user menu > Downloads): any signed-in user downloads the Windows player, the Android APK and the PowerPoint add-in, with version, size and SHA-256. Admins upload and remove files (audited). Files are stored outside the media folder under random names and served as attachments. Migration 0015. Later: the kind and version of an upload are detected automatically (exact from an APK, from the file name for installers; you can type them), and the newest version of each kind is what the install link, the QR set-up and **Push the update** use.

## Unreleased: Server installers

### Added
- **Install the server as a service that starts with the computer:** `scripts\install-server.ps1` (Windows: a Scheduled Task running as the low-privilege `LOCAL SERVICE` account that restarts on failure, a firewall rule for private and domain networks) and `scripts/install-server.sh` (Linux with systemd, hardened; the macOS launchd branch is a leftover, macOS is dropped and unsupported). Each finds or installs Python 3.10+, offers ffmpeg and a browser, keeps the program and your data apart, writes a settings file, applies the database migrations, starts the server and prints the address. Run again to upgrade (database backed up first, previous version kept as `app.prev`); `--uninstall` removes it and keeps your data (`--purge` deletes it). See [docs/installing-the-server.md](docs/installing-the-server.md).
- Tested here: the Windows script's non-elevated parts (copy, environment, migrations, backup, the installed server answering). Not run yet: the Scheduled Task, firewall rule and permissions (need administrator), and the Linux script on a real machine.

## Unreleased: Device agent

### Added
- **Device agent settings** (admins, **Displays > Edit > Device agent**, all off by default): watchdog, allow "Restart the player", allow "Collect logs". They reach the apps in the manifest (`settings.agent`).
- **Durable command queue** for the apps' device agent: an admin issues a command from the display page, it is stored (the database row is the truth), the screen is told to look, fetches only its own commands (`GET /commands`), runs each at most once and acknowledges it (`POST /commands/<id>/ack`). Commands expire by server time, there is one open command per action and a cap per hour, and every step is audited. A command needs the admin's switch **and** the app reporting that it can do it. Database migration `0014_device_agent`, job `agent.commands`.
- The display page shows what each screen's app reports (agent version, privilege tier, watchdog activity) and the last commands.
- **Collect logs:** an admin asks for a screen's log from the display page; the app uploads it (redacted, gzip, at most 1 MB) only for that open request. The server stores it as is, keeps the newest 5 per display for 7 days, and admins download it from the display page.
- **Windows app:** the device agent. A per-user Scheduled Task watchdog (no administrator needed) restarts the player after a crash or hang within about a minute, at most 6 times per hour, then reports that it gave up. Admins can restart the player and collect logs from the display page. The log rotates to a `.1` file instead of truncating.
- **Android app:** the device agent. A hang watchdog (reload the page after 45 s without a sign of life, then a new WebView, a new activity, a process restart, capped per hour), remote **Restart the player** and **Collect logs**, a crash handler, the previous exit reasons in the log, and the player starts after an app update when kiosk mode is configured. A process restart is only reliable as Device Owner or in Home mode; without them Android may refuse to start the app from the background.
- The web player tells the app it is alive every 10 seconds (`alive` on the shell hook) and the app reports its abilities (`stats().agent`) in the diagnostics. Everything is **off by default** per display and admin-only. Reboot and update commands came later as signed commands (see "Signed device commands").
- Not run on real hardware yet: both apps' watchdogs and commands are covered by unit tests, and the Windows watchdog script and Scheduled Task were exercised on this PC with a stand-in process. Manual test checklists are in each app's README.

## Unreleased: Player fixes and the PowerPoint add-in

### Changed
- **News reader and stock ticker stay in step on every screen.** Their headline rotation, progress bar and ticker scroll now run on the shared server clock instead of a timer that started when each screen happened to load (so the web player could be a headline apart from the apps). Plugins also read the same round-trip-corrected clock as the player, and have a new `ctx.aligned(fn, ms)` timer that fires on server-clock boundaries. The image library's slideshow and the weather radar's animation still run on local timers.
- **YouTube start and end times are kept when the video loops.** A video set to play "from 0:30 to 1:30" ran to its real end on synced screens: with looping on, YouTube ignores the end time (and restarts from 0). The player now keeps a looping clip inside its start and end itself.
- **Synced YouTube is now held in step:** on screens of a group with synchronized playback, a single YouTube video is checked against the shared timeline using the position the embed reports, and seeked back when it drifts by more than about 0.6 s (at most every 4 s). Screens end up within roughly half a second of each other. Lists, playlists and live streams only start together. This uses YouTube's own player API; nothing is downloaded.
- **Fast forward and rewind** (arrow keys, remote, wheel, double click): the hint now says "Fast forward +10 s" or "Rewind −10 s", and a short video uses a smaller step (a third of its length, at least 2 s). On images, web pages and plugins the arrows still change the slide ("Next" / "Previous").
- **Mouse:** press and drag on a video to fast forward or rewind it, like on a touch screen. A plain click still pauses.
- **Next and previous by hand** (keys, swipe, remote) slide the new slide in from the right or left, whatever the playlist's transition is. Automatic changes keep the playlist's transition.
- **Android:** the Back key now closes the app when **Lock player** is off (with it on, Back still does nothing). The mouse's side button remains a control.

### Added
- **PowerPoint add-in** (VSTO, `clients/powerpoint-addin`): exports the open presentation to MP4 and uploads it, or replaces an existing media item, through the API with a token. The API layer is checked against a real server by a smoke test (`tests/ApiSmoke`); a README covers building, installing and a manual test checklist for PowerPoint itself, which has not been run yet. Version 3.0.2 adds the AiSignX icon (ribbon button, window title bars) and a one-click installer launcher (`Install AiSignX for PowerPoint.exe`, built by `tools/Build-Installer.ps1`) that trusts the certificate and runs the setup, a full user guide ([docs/powerpoint-addin.md](docs/powerpoint-addin.md)) and an install note in the zip. Version 3.0.1: the dialog scrolls and keeps its status line and buttons pinned at the bottom (on a small or scaled screen they were off the edge, so **Test connection** seemed to do nothing); **Test connection** now also answers in a message box, and an unexpected error in a button is shown instead of being lost.

## Unreleased: Synchronized playback

### Added
- **Synchronized playback** for a group of displays (**Groups > Edit > Synchronized playback**): the group's screens show the same slide at the same moment. Each screen works out the current slide from the shared server clock and the playlist's durations, so it needs no extra traffic, survives restarts and network gaps, and a screen with a wrong clock is fine (only its distance from the server clock matters, estimated from the answers it already gets, corrected by round trip). Videos join at the right position and are pulled back if they drift; a screen that joins late jumps to where the group is. Player controls are off on synced screens, and slides run for their set duration. A YouTube video loops for the whole slide (it cannot end the slide early; a video that ended early used to leave the screen empty until the slide's time was up). Display pages show "Synced playback" with each screen's clock offset. Database migration `0013_group_sync`. See [docs/synced-playback.md](docs/synced-playback.md).

## Unreleased: Admin interface makeover

### Changed
- **New layout:** a left sidebar replaces the top bar. Menus are grouped: Dashboard, Content (Media, Plugins, Playlists), Screens (Displays, Groups, Schedules, Emergency), Monitoring (Alerts, Reports, Downloads) and, for admins, Administration (Users, Settings, All API tokens, Audit log, Backups, Updates, System, Logs). The sidebar collapses to icons (the choice is remembered), and on phones and tablets it slides in over the page.
- **Light and dark themes:** a button in the sidebar cycles *follows the system*, *light* and *dark* (remembered per browser, applied before the page paints, so nothing flashes). Icons are built in (no network needed).
- **Media library:** drag and drop images and videos anywhere on the page (or click the drop zone), many at once. Each file gets its own progress bar (two upload at a time), problems are shown per file (wrong type, too big, not a valid image), and the page refreshes when everything is in. Filter chips with counts (All, Images, Videos, Web pages, Plugins) and the search box work instantly, with no page reload. Without JavaScript the plain upload form still works.
- **Display pages:** the edit page is now titled cards (General, Screen, Controls and lock) in two columns with a Save bar that stays in view; the display page uses the full width, with the status and settings on the left and the screenshot, playback check, next 7 days and diagnostics on the right.

## Unreleased: Video playback warnings

### Added
- The server records each video's codec, profile, level, pixel format, frame rate and bitrate (ffprobe). Videos that many players cannot decode smoothly (4K or other frames over Full HD, bitrate above 20 Mbps, HEVC/VP9/AV1, 10-bit or 4:2:2, over 60 fps) get a **May stutter** badge in the media library, a Playback note on the edit page, and a warning after upload. Videos uploaded earlier are read in the background (job `media.probe`). Database migration `0011_video_info`.
- **Players report what they cannot decode.** Each player asks its own browser (the same decoder it plays with) whether it can play the videos in its playlist smoothly, and sends only the problem ones with its diagnostics. The Displays page shows a **May stutter** badge, and the display page lists the videos with their size and codec and what the screen said ("cannot decode it" or "may stutter"), with a pointer to *Make TV-friendly copy*. Needs no app update beyond the web player; browsers without the capability check report nothing.
- **Make TV-friendly copy** (media edit page, needs ffmpeg on the server): re-encodes a video in the background to Full HD H.264 at about 8 Mbps and at most 30 frames per second, with a keyframe every 2 seconds (fast forward and rewind stay quick) and the index at the front. A progress bar follows it. The original is kept, or replaced in place (playlist placements stay) if you tick **Replace**. One conversion runs at a time. Audited as `media.convert`.

## Unreleased: Plugins

### Added
- **Plugin items** in the media library (**Add plugin**): live content that plays in playlists like any other item, with its own duration, transition and proof of play. Seven plugins ship with AiSignX; there is no plugin upload. See [docs/plugins.md](docs/plugins.md).
  - **Clock:** five styles (minimal dark and light, analog, flip, word clock), 12h/24h, seconds, date, time zone and label. Works offline.
  - **Weather:** current conditions, hourly and daily forecast in three layouts. Open-Meteo (a key in `AISIGNX_OPENMETEO_KEY` for commercial use) or MET Norway (needs a contact for the User-Agent). Place names and postal codes are looked up when you save, or type `lat,lon`.
  - **Weather radar:** animated radar over a map, drawn on the server from RainViewer (worldwide) or IEM NEXRAD (US) tiles, with optional US weather alerts. The OpenStreetMap base map can be replaced by your own tile server.
  - **RSS / Atom:** headlines as a list, one at a time or a ticker, with pictures and QR codes. The feed address stays on the server; intranet feeds need an admin allowlist.
  - **Stocks:** quotes from Finnhub as cards, a board or a ticker. Needs `AISIGNX_FINNHUB_KEY`; the key never reaches a screen. The admin page explains Finnhub's terms for public display.
  - **YouTube:** videos (with start and end times), a playlist or a live stream in the privacy-enhanced embed, with shuffle, mute, captions and "play to the end".
  - **Image library:** a slideshow, Ken Burns show or 2×2 / 3×3 collage of library images you choose or the newest ones, with an automatic duration.
- **API keys on the Plugins page:** admins enter the Finnhub (stocks) and Open-Meteo keys in the plugin's card. They are stored encrypted in the database, never shown again and never sent to screens. An environment variable still wins if set. No restart needed. Adds the `cryptography` dependency (run `pip install -r requirements.txt`).
- **Mute and volume in the playlist:** each video and each YouTube item in a playlist has a **Mute** box and a **Volume** slider (0-100%, a share of the display's volume). The YouTube plugin's own Mute option is gone; existing YouTube items that played with sound stay unmuted in their playlists. Database migration `0010_item_volume`.
- **Plugins page** (`/plugins/`): version, outbound hosts, data terms, API key status and cache size per plugin. Admins turn plugins on or off, set provider settings, a contact for data providers and a locale, and clear the plugin cache. All changes are audited and reach screens at once.
- **Preview** of plugin items at 16:9 and 9:16, also before saving.
- **Server-side data:** the server fetches and caches plugin data in the background (stale-while-revalidate), so a slow provider never holds up screens or the admin. Outbound requests go through a guarded client: per-plugin host allowlist, private addresses refused, redirects re-checked, size and time limits, per-host rate limits, an optional proxy (`AISIGNX_PLUGIN_HTTP_PROXY`). The cache (`AISIGNX_PLUGIN_CACHE_DIR`, `AISIGNX_PLUGIN_CACHE_MB`) is pruned daily and never backed up.
- **Sandboxed player:** most plugins run in a sandboxed frame with no network, cookies or storage; the player passes them their data. Screens keep each item's last data and show it with an "Updated" time while offline.
- Player API: items of `type: "plugin"` with a `plugin` block, and `GET /plugins/<id>/data` and `/plugins/<id>/assets/<asset>` ([docs/player-api.md](docs/player-api.md#plugin-items)).
- Windows and Android apps: the images of image library items are cached for offline use and kept like image items.
- Deployment guide: environment variables, outbound hosts for a firewall, and provider terms ([docs/deployment.md](docs/deployment.md#plugins)).
- Database migration `0009_plugins` (adds the plugin key and config to media). Downgrading removes plugin items.

### Changed
- Changing a plugin's organization setting (for example the weather provider or radar source) makes screens show the new provider's data on their next refresh instead of after the old data expires.

### Fixed (content is never interactive)
- No click, tap, hover, scroll, text selection or key reaches a web page, a YouTube video, a plugin, a video or an image on a screen, whatever the display's settings: the content is switched off for pointer input in CSS, frames are marked `inert`, and the player starts locked so nothing is touchable in the moment before the first settings arrive. The player's own controls (when switched on for a display) still work through their own layer above the content.

### Fixed (web pages that refuse to be embedded)
- Sites that forbid being shown inside another page (google.com, most dashboards, anything with a login) showed only an error on screens. A web page now has **How to show it**: *Automatic* (the default) checks the site's headers once a day (`X-Frame-Options`, `frame-ancestors`) and plays a page that forbids it as a **screenshot** of the page, retaken by the server's browser every 5 minutes (adjustable, 1-1440); *Live page* always embeds it; *Screenshot* always uses one. Screenshots are Full HD images, so every app and browser can show them and the apps cache them. A screenshot does not scroll or animate. Media cards show a **Screenshot** badge, the edit page shows the status and has **Check the page and retake the screenshot**. A page whose screenshot is not ready yet is left out of the playlist for the moment. Needs Edge, Chrome or Chromium on the server (as for previews). Existing web pages are checked automatically. Database migration `0012_web_pages`, job `media.webpages`.

### Fixed (offline and server restarts)
- The player no longer blanks to "None of the items could be played" when every other item fails to load (server down); it keeps the slide that is playing and tries the others again every ~10 s, and carries on as soon as they load.
- YouTube only shows "This screen is offline" when the device itself has no network. The AiSignX server being down or restarting no longer stops it, because the video comes straight from YouTube. A YouTube slide that never answers (no internet) now moves on after a few seconds instead of holding the playlist for its full duration.
- Fast forward, rewind and drag-to-scrub on big videos: a key press or drag step that arrives while the video is still seeking no longer cancels that seek (on a long-GOP file every restarted seek decodes from the last keyframe again, so the picture froze). Presses during a seek are combined into one jump, and a video that is busy seeking is no longer reported as "stalled".
- The offline banner (and the offline state plugins see) now appears within about 12 seconds of the server becoming unreachable, instead of after 75 seconds or more: the player checks the server every 10 seconds while the live connection is down, and one failed check is enough. It went unseen before because a restart or a short outage ended inside that window. It disappears as soon as the server answers again.
- YouTube captions are really off when the option is off. Before, YouTube still showed them whenever the screen's or account's own caption setting was on.
- Plugin runner files can be reused by the browser for an hour, so a plugin with saved data can still start while the server restarts.
- Still open: the plain web player cannot start a *new* plugin slide when the server has been down for more than an hour (the apps are better placed; their plugin data and asset caching is planned with the device agent work).

### Removed (compared with v2 plugins)
- Weather: NWS and zippopotam lookups, the debug overlay. Radar: nowcast and on-screen controls. Stocks: the `loops` option (the playlist sets the duration). YouTube: playback rate, quality, branding and per-session modes. Per-tenant plugin policy, signing and zip upload.

### Not yet checked
- In the Windows and Android apps: the frame sandbox and offline start with each plugin, YouTube with sound and without error 153 (the Windows app's `127.0.0.1` referrer), the ended and error messages from YouTube, and pause and next on plugin slides. Checked so far in headless Chrome and Edge only.
- Live provider requests (all tests use recorded fixtures).

## Unreleased: Player controls and Lock player

### Added
- **Player controls** (the display option formerly called "Allow touch, mouse and keyboard"): tap, Space or OK pauses and resumes; arrows seek 10 s in a video or change item; swipe, PageUp/PageDown, the remote's up/down and media keys change item. A paused screen resumes on its own after **Resume after a pause** (1-60 minutes, default 5), and an emergency ends any pause. Paused time does not count in proof of play; a skipped item is reported with its real on-screen time.
- **Lock player** (admins only, Windows and Android apps): the app cannot be left without the unlock PIN. It needs a PIN, and the PIN cannot be removed while the lock is on. Android uses lock task mode (full lockdown as Device Owner). See [docs/kiosk-lockdown.md](docs/kiosk-lockdown.md).
- **Remote unlock** on the display page: lifts the lock for 5 minutes to 1 hour, or ends it early with **Lock again**. Both are audited.
- Player API: `settings.kiosk_lock` and `settings.resume_after_s`; the `unlock` and `lock` commands; shell hooks `onCommand` and `onControl`, the caps `controls` and `kiosk-lock`, and `stats().lock` (shown in Diagnostics).

### Changed
- With Player controls on, a transparent layer covers every item, so web pages in a playlist can no longer be clicked or scrolled from the screen.

## Unreleased (v3)

v3 is a clean rewrite of AiSignX as a single-organization app.

### Added
- Phase 1 (Core): Flask app factory, environment-based config, Alembic migrations.
- First-run setup wizard that creates the admin account, organization name and time zone (no default passwords).
- Login and logout (POST with CSRF), "keep me signed in", safe `next` redirects, and a profile page for changing your password.
- User management for admins: create, edit, disable and delete users with the roles `admin`, `editor` and `viewer`. Admins cannot demote, disable or delete themselves.
- Settings page (organization name, time zone).
- Bootstrap 5 base layout served locally (works offline) and a `/healthz` endpoint.
- pytest suite that builds the schema through Alembic migrations.
- Phase 2 (Content): media library with multi-file image and video upload, web pages by URL, file validation (Pillow / ffprobe), thumbnails, detected video length and resolution, search and type filter.
- Playlists with drag-and-drop ordering (plus up/down buttons), per-item duration, transition, fit mode and video mute, a playlist default transition (including random), duplicate, and total runtime.
- Setting for the default duration of new images and web pages.
- Web page previews: screenshots taken in the background with the installed Edge, Chrome or Chromium (headless, no extra Python packages), a title card when a page can't be captured, a "Refresh preview" button, and live refresh in the media list.
- Install scripts `scripts/install.ps1` (Windows, winget) and `scripts/install.sh` (Linux/macOS) that install Python packages, ffmpeg and a browser if missing, then set up the database.
- `flask --app wsgi check` command and an admin dashboard warning for missing programs.
- Uploads are stored in `server/uploads` (`AISIGNX_UPLOAD_DIR`), with a size limit from `AISIGNX_MAX_UPLOAD_MB` (default 2048).
- Phase 3 (Screens): Displays page with online/offline status, last seen, IP, player version and a "live" indicator, refreshed every 15 seconds. Editors can pair, edit, reload, unpair and delete displays; viewers can look.
- Pairing with a short code: a screen opens `/player/`, shows a code such as `K7P-Q4X`, and an editor enters it under **Displays > Pair a display**. Codes are single-use and expire after 10 minutes. "Pair as an existing display" replaces the hardware but keeps the name and playlist.
- Browser player at `/player/`: full-screen playback of images, videos and web pages with the item's duration, fit mode, mute setting and transition; preloads the next item, skips items that fail, and keeps playing from cache when the server is offline.
- Live updates over Server-Sent Events: playlist, media and display changes reach screens within about a second, with a 30-second heartbeat as the fallback. A **Reload** button refreshes a screen's page.
- Player API at `/api/player/v1` for the browser player and the future Electron and Android clients (see `docs/player-api.md`). Display tokens are stored only as hashes and never appear in URLs; the browser keeps its token in an HttpOnly cookie.
- `flask --app wsgi serve` runs the waitress production server with enough threads for the screens (`AISIGNX_MAX_STREAMS`, default 50, plus 16).
- `AISIGNX_PROXY_HOPS` for running behind nginx, Caddy or IIS; `docs/deployment.md` covers proxy settings, HTTPS and kiosk browsers.
- Dashboard counts for displays and screens online.
- Phase 4 (Scheduling): schedules that play a playlist on all screens, a group or one display, on chosen weekdays, all day or in a time window (overnight windows allowed), between optional dates, with a priority from 0 to 100. Times use the organization's time zone, including correct daylight saving changes. See `docs/scheduling.md`.
- Schedule list with "Playing on N" and "Shadowed" badges, plus enable/disable and duplicate. Each display page shows what plays over the next 7 days.
- Display groups: a display can belong to one group; groups can be the target of schedules and emergencies.
- Emergency override: send a message (info, warning or critical) to all screens, a group or one display, as a full-screen text, a banner over an emergency playlist, or the playlist alone. It stays until cleared, also on screens that go offline. A red banner on every admin page shows live emergencies, and the page keeps a history with "Send again".
- Player API: the manifest gains `source` (why this playlist plays) and `emergency`; new events `emergency` and `emergency_clear`. Existing keys are unchanged.
- A background ticker pushes schedule starts and ends to screens within about a second (`AISIGNX_SCHEDULE_TICKER`, on by default).
- Dashboard counts for enabled schedules and groups, and an alert while an emergency is live.
- Phase 5 (Clients): AiSignX Player for Windows (Electron, `clients/electron-client`) and for Android 8.0+ including TV boxes (`clients/android-client`). Both are kiosk shells around the browser player at `/player/`, so pairing and playback work exactly as in a browser. See `docs/clients.md`.
- Player apps: setup screen with a `/healthz` connection test, an http warning for public addresses, and **Forget server**. No password, token or code is typed on the device.
- Player apps: HTTPS trust through the system certificate stores, or a SHA-256 certificate pin accepted in setup for self-signed servers. A changed certificate is refused and shows "Server certificate changed".
- Player apps: offline start-up from a saved copy of the player page, and a media cache (size and checksum verified, LRU, files in the current playlist never evicted) so screens keep playing while the server is down.
- Electron: loopback caching proxy, kiosk window with blocked keys and Ctrl+Alt admin shortcuts, single instance, keep-awake, crash and hang watchdogs, Windows login item, NSIS installer (`npm run dist:win`).
- Android: full-screen WebView with immersive mode and keep-awake, remote keys blocked, hidden gesture back to setup, optional Home-app mode and a boot receiver, renderer-crash recovery. Release builds are signed only when `keystore.properties` exists.
- Player shell hook (`docs/player-api.md`, "Shell hook"): the player reports `electron-<version>` or `android-<version>` and the device name, uses the app's client ID, shows the real server address in the pairing hint, and tells the app when it is paired and when a new manifest arrives. No credential passes through the hook.
- Display options: location and notes (admin only, never sent to the screen), orientation (0/90/180/270), a fit mode for every item, video volume, an offline banner, and an input lock. Admins can also set a 4-8 digit unlock PIN that the Windows and Android apps ask for before leaving the player; it is stored and sent only as a salted PBKDF2 hash, with a growing lockout after 5 wrong PINs.
- Display page: **Identify** shows the display name on the screen, **Screenshot** asks the screen for a picture (the apps capture the real screen, a browser draws its own media), and a **Diagnostics** card shows screen size, app version and capabilities, cache, free storage and recent player errors.
- Player API: the manifest gains `settings`; new `command` actions `identify`, `screenshot` and `diagnostics`; `POST /screenshot`; an optional `diag` object in the heartbeat; shell `caps`, `capture()` and `stats()`. Existing keys are unchanged.
- Phase 6 (Operations): see [docs/operations.md](docs/operations.md). A background jobs thread (`AISIGNX_JOBS`) runs alerts, scheduled backups, retention and rollups; each job's last run and error are kept in the database so restarts don't repeat work. New settings for instance, log and backup folders (`AISIGNX_INSTANCE_DIR`, `AISIGNX_LOG_DIR`, `AISIGNX_LOG_LEVEL`, `AISIGNX_BACKUP_DIR`) and a rotating log file. An admin menu (audit log, backups, system, logs, all API tokens) and top-level **Alerts** and **Reports**.
- Audit log viewer (filters, CSV export, retention) and audit entries for setup, sign-in, users, displays, groups, playlists, schedules, media, emergency messages, settings, tokens, backups and alerts, with History links on edit pages. Passwords, tokens, PINs and keys are never recorded.
- Login rate limiting: failed sign-ins limited per IP address and per user name (429 with Retry-After; `AISIGNX_LOGIN_MAX_PER_IP`, `AISIGNX_LOGIN_MAX_PER_USER`, `AISIGNX_LOGIN_WINDOW_MIN`). Failed API token checks count against the same per-IP limit.
- API tokens (per user, scoped `media:read`/`media:write`, optional expiry, revocable, stored only as hashes) and a JSON media API at `/api/v1/media` with the PowerPoint add-in's `/api/media` paths: list, get, upload and replace. See [docs/api.md](docs/api.md).
- Replace a media file in place (API and admin) keeping its name, duration and playlist places; media IDs shown in the admin; a daily cleanup of unused upload files.
- Backups: ZIP archives (SQLite backup API, secret key, uploads, sha256 manifest), a daily schedule with retention, web upload, download and restore with maintenance mode, a safety backup (always with the uploads), a disk-space check, automatic rollback and sign-out of every session, and `flask backup create|list|verify|restore|prune`. See [docs/backup.md](docs/backup.md).
- Alerts derived from facts (display offline, low disk, sign-in burst, backup failed or stale, failing jobs) with acknowledge, per-display mute and optional SMTP e-mail (password only from `AISIGNX_SMTP_PASSWORD`).
- Proof of play: screens report finished items (`POST /api/player/v1/plays`, off by default), an hourly daily rollup in the organization's time zone (a deleted display keeps its totals), **Reports > Plays** with daily and raw CSV exports, and retention settings.
- System page: self-test with timings (database, schema, folders, disk, ticker, jobs, live connections, last backup, SMTP), runtime info, a log viewer with level filter and download, and `flask --app wsgi check --all [--json] [--smtp]`.
- Docker image and compose file (one `/data` volume, non-root user, health check, optional Caddy HTTPS profile); [docs/https.md](docs/https.md) and [docs/docker.md](docs/docker.md). Web previews honour `AISIGNX_BROWSER_NO_SANDBOX`.

### Changed
- **Input on screens is now off by default.** Touches, clicks, keys and the wheel are ignored, so web pages in playlists can no longer be used from the screen until an admin turns on **Allow touch, mouse and keyboard** for that display. Screens on an older server keep input on.
- The install scripts now suggest `flask --app wsgi serve` to start the server.
- SQLite uses WAL mode and a 15-second busy timeout, so screen heartbeats and admin edits don't block each other. Keep the database on a local disk.
- API errors under `/api/` (404, 405, 413 and others) return JSON instead of HTML pages.
- A display's playlist is now called its **Default playlist**: it plays when no schedule matches. The displays list shows what each screen is playing now and why.
- A playlist used by a schedule or a live emergency can no longer be deleted. Deleting a display or group also removes its schedules.
- Changing the time zone in Settings updates all screens right away.
- `/healthz` now returns `{"status": "ok", "app": "aisignx", "api": 1}` so the player apps can recognise an AiSignX server. It still needs no login and shows no version.
- `/healthz` returns HTTP 503 with `"status": "error"` when the database cannot be reached (same keys as before).
- HTTPS and reverse-proxy instructions moved from `docs/deployment.md` to `docs/https.md`.
- The admin navigation collapses into a menu on screens narrower than 992 px.
