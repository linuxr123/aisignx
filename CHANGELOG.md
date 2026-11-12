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

### Changed
- The install scripts now suggest `flask --app wsgi serve` to start the server.
- SQLite uses WAL mode and a 15-second busy timeout, so screen heartbeats and admin edits don't block each other. Keep the database on a local disk.
- API errors under `/api/` (404, 405, 413 and others) return JSON instead of HTML pages.
