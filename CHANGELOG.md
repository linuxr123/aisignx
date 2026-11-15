# Changelog

## Project status (updated 2026-11-15)

A quick overview. The detailed list of every change follows further down.

### Done
- **Core:** setup wizard (no default passwords), sign-in, users and roles (admin, editor, viewer), settings.
- **Content:** media library (images, videos, web pages with screenshot previews), playlists with drag-and-drop, durations, transitions and fit modes.
- **Screens:** pairing with a short code, browser player at `/player/`, live updates within about a second, online/offline status.
- **Scheduling:** schedules by day, time and date, display groups, emergency messages.
- **Player apps:** Windows (Electron, current installer 3.1.0) and Android (phones and TVs), with offline caching and certificate pinning.
- **Display options:** orientation, volume, offline banner, location, exit PIN, Identify, remote screenshots, diagnostics.
- **Player controls:** pause, next/previous and seek by keyboard, mouse, touch and TV remote. **Lock player** kiosk mode with remote unlock.
- **Operations:** backup and restore, audit log, alerts by e-mail, proof of play, API tokens and a media upload API, health checks and logs, Docker, HTTPS guide.
- **Plugins:** the plugin framework and seven plugins: clock, weather, weather radar, RSS / Atom, stocks, YouTube and image library.
- **Fixed 2026-11-14:** videos whose MP4 index is at the end of the file (e.g. "sample-10s") stalled on Android and Windows. Uploads are now rewritten with the index at the front (lossless), and the existing file was fixed.

### In progress
- **Plugins:** the manual checks in the Windows and Android apps (see "Not yet checked" below).
- **PowerPoint add-in:** export a presentation to MP4 in PowerPoint and upload or replace it with an API token.
- **Designs being written:** the device agent/watchdog, and content extras.

### Planned (in this order)
1. **Admin interface makeover** (moved up on 2026-11-15):
   - a navigation bar on the left that collapses to icons;
   - light and dark themes;
   - better-organized menus (system settings, admin settings, logs);
   - the media library as a grid with filters, and drag-and-drop upload of many files at once;
   - a nicer display edit page.
2. **PowerPoint add-in:** finish and test it (export to MP4, upload or replace with an API token).
3. **Player fixes:**
   - The arrow keys always mean fast forward/rewind (smaller steps for short videos), and the hints say "Fast forward"/"Rewind". "Next"/"Previous" only appear when changing slides.
   - Mouse: press and drag to fast forward or rewind a video, like on a touch screen. A plain click still pauses.
   - Going to the next or previous slide by hand slides left or right, whatever the playlist's transition is. Automatic changes keep the playlist's transition.
   - Back on Android can exit when Lock player is off.
   - The offline banner shows sooner (setting).
   - The apps' video caches handle range requests correctly.
   - Android: fast forward and rewind work poorly (reported 2026-11-15); fix and test on a device.
4. **Web pages that refuse to be embedded** (e.g. google.com):
   - a warning when added;
   - a "Show as screenshot" option with periodic refresh;
   - an automatic fallback in the player;
   - optional live mode in the Windows app.
5. **Device agent and watchdog** (Windows and Android):
   - restarts the player when needed, installs signed updates, reboots, collects logs;
   - Windows kiosk setup (auto-login, replace the shell), all controlled from the display settings.
6. **Emergency improvements:**
   - saved emergency templates;
   - scheduled emergencies;
   - triggering an emergency from another device (API, button or webhook).
7. **Synced playback** between displays (from v2). Player controls are turned off on synced displays, because pause, next or previous on one screen would break the sync.
8. **Content extras:** smart playlists (filled by tags), media tags and folders, a schedule conflict view.
9. **Later, maybe:** multiple sites and single sign-on (Phase 7, on hold).

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

### Fixed (offline and server restarts)
- The player no longer blanks to "None of the items could be played" when every other item fails to load (server down); it keeps the slide that is playing and tries the others again every ~10 s, and carries on as soon as they load.
- YouTube only shows "This screen is offline" when the device itself has no network. The AiSignX server being down or restarting no longer stops it, because the video comes straight from YouTube. A YouTube slide that never answers (no internet) now moves on after a few seconds instead of holding the playlist for its full duration.
- Fast forward, rewind and drag-to-scrub on big videos: a key press or drag step that arrives while the video is still seeking no longer cancels that seek (on a long-GOP file every restarted seek decodes from the last keyframe again, so the picture froze). Presses during a seek are combined into one jump, and a video that is busy seeking is no longer reported as "stalled".
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
- Phase 6 (Operations): see [docs/operations.md](docs/operations.md). A background jobs thread (`AISIGNX_JOBS`) runs alerts, scheduled backups, retention and rollups; each job's last run and error are kept in the database so restarts don't repeat work. New settings for instance, log and backup folders (`AISIGNX_INSTANCE_DIR`, `AISIGNX_LOG_DIR`, `AISIGNX_LOG_LEVEL`, `AISIGNX_BACKUP_DIR`) and a rotating log file. An **Admin** menu (audit log, backups, system, logs, all API tokens) and top-level **Alerts** and **Reports**.
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
