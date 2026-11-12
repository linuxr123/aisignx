# Changelog

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

### Changed
- The install scripts now suggest `flask --app wsgi serve` to start the server.
- SQLite uses WAL mode and a 15-second busy timeout, so screen heartbeats and admin edits don't block each other. Keep the database on a local disk.
- API errors under `/api/` (404, 405, 413 and others) return JSON instead of HTML pages.
- A display's playlist is now called its **Default playlist**: it plays when no schedule matches. The displays list shows what each screen is playing now and why.
- A playlist used by a schedule or a live emergency can no longer be deleted. Deleting a display or group also removes its schedules.
- Changing the time zone in Settings updates all screens right away.
- `/healthz` now returns `{"status": "ok", "app": "aisignx", "api": 1}` so the player apps can recognise an AiSignX server. It still needs no login and shows no version.
