# AiSignX

Self-hosted digital signage. One web admin manages playlists, schedules and screens,
for a home, a small business, or a company with a few dozen screens.

> **Status:** v3 is a rewrite in progress. Phase 1 (accounts, roles, settings, setup wizard),
> Phase 2 (media library, playlists), Phase 3 (screens: pairing, browser player, live updates),
> Phase 4 (schedules, display groups, emergency override), Phase 5 (Windows and Android player
> apps) and Phase 6 (backups, alerts, audit log, API, proof of play, Docker) are done, plus
> plugins (clock, weather, radar, RSS, stocks, YouTube, image library).
> See [CHANGELOG.md](CHANGELOG.md).

## Requirements

- Python 3.10 or newer
- [ffmpeg](https://ffmpeg.org/): reads video length, resolution, codec and bitrate, makes video thumbnails, and makes TV-friendly copies of heavy videos (4K, very high bitrate).
- A Chromium-based browser (Microsoft Edge, Google Chrome or Chromium): takes screenshots of web
  pages for their previews. It runs in headless mode, so no browser window opens.

The install scripts check for all of these and install whatever is missing.

## Install

**Windows** (uses `winget` for anything missing):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

**Linux or macOS** (uses apt, dnf, pacman, zypper or Homebrew):

```bash
./scripts/install.sh
```

Add `-Yes` (Windows) or `-y` (Linux/macOS) to install without asking. The scripts are safe to
run again. Then start the server:

```powershell
cd server
.venv\Scripts\flask --app wsgi serve      # Linux/macOS: .venv/bin/flask --app wsgi serve
```

`serve` runs the production web server (waitress) with enough threads for your screens.
`flask --app wsgi run` also works for a quick test at home.

Open http://127.0.0.1:5000. On first start, the setup wizard asks for your organization name,
time zone and the first admin account. There are no default passwords.

To check the system at any time, run `flask --app wsgi check --all` in `server/`, or open
**Admin > System**. Admins also see a warning on the dashboard if a required program is missing.

To run AiSignX in Docker instead, see [docs/docker.md](docs/docker.md).

## Configuration

Settings come from environment variables or `server/.env` (see `server/.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `AISIGNX_SECRET_KEY` | generated in `server/instance/secret_key` | Session signing key |
| `AISIGNX_DATABASE_URL` | SQLite in `server/instance/aisignx.db` | Any SQLAlchemy URL (e.g. Postgres) |
| `AISIGNX_SECURE_COOKIES` | `0` | Set to `1` when serving over HTTPS |
| `AISIGNX_UPLOAD_DIR` | `server/uploads` | Where uploaded media is stored |
| `AISIGNX_MAX_UPLOAD_MB` | `2048` | Largest accepted upload |
| `AISIGNX_FFMPEG` / `AISIGNX_FFPROBE` | found on PATH | Paths to the ffmpeg tools |
| `AISIGNX_BROWSER` | auto-detected | Edge, Chrome or Chromium used for web page previews |
| `AISIGNX_WEBSHOT_TIMEOUT` | `45` | Seconds allowed per web page screenshot |
| `AISIGNX_MAX_STREAMS` | `50` | Most screens with a live-update connection at once; `serve` starts this many threads plus 16 |
| `AISIGNX_PROXY_HOPS` | `0` | Number of reverse proxies in front of AiSignX; set to `1` behind nginx, Caddy or IIS |
| `AISIGNX_SCHEDULE_TICKER` | `1` | Background check that pushes schedule starts and ends to screens each minute; `0` turns it off (screens then catch up through their 30-second heartbeat) |
| `AISIGNX_INSTANCE_DIR` | `server/instance` | Folder for the SQLite database and `secret_key` |
| `AISIGNX_LOG_DIR` / `AISIGNX_LOG_LEVEL` | `server/logs` / `INFO` | Log folder (`aisignx.log`, 5 x 5 MB) and level |
| `AISIGNX_BACKUP_DIR` | `server/backups` | Where backup archives are written |
| `AISIGNX_JOBS` | `1` | Background jobs (alerts, backups, retention); `0` turns them off |
| `AISIGNX_SMTP_PASSWORD` | not set | Password for the alert mail server (never stored in the database) |
| `AISIGNX_LOGIN_MAX_PER_IP` / `_PER_USER` / `_WINDOW_MIN` | `20` / `5` / `15` | Failed sign-ins allowed per IP and per user name within the window in minutes |
| `AISIGNX_BROWSER_NO_SANDBOX` | `0` | Start the preview browser with `--no-sandbox` (containers running as non-root) |
| `AISIGNX_PLUGIN_CACHE_DIR` / `_CACHE_MB` | `server/plugin-cache` / `500` | Cache of fetched plugin data (never backed up) and its size cap |
| `AISIGNX_PLUGIN_HTTP_PROXY` | not set | HTTPS proxy for the server's outbound plugin requests |
| `AISIGNX_OPENMETEO_KEY` / `AISIGNX_FINNHUB_KEY` | not set | API keys for commercial Open-Meteo weather and for stock quotes (see [docs/plugins.md](docs/plugins.md)) |

The folder defaults are relative to the instance folder's parent, so with
`AISIGNX_INSTANCE_DIR=/data/instance` logs go to `/data/logs` and backups to `/data/backups`.

## Media and playlists

- **Media:** upload images (JPG, PNG, GIF, WebP, BMP) and videos (MP4, WebM, MOV, M4V, MKV), or
  add a web page by URL. Files are checked on upload and stored under random names.
- **Web page previews:** a headless browser takes the screenshot in the background, so it appears
  a few seconds after you add the page. Use **Refresh preview** on the edit page when the site
  changes. If the page can't be loaded, a title card is shown instead.
- **Playlists:** drag items to reorder them. Each item can override its duration, transition,
  fit mode (fit, fill or stretch) and, for videos, mute. Videos play for their full length unless
  you set a duration.
- **Plugins:** **Add plugin** in the media library creates a live item: a clock, the weather, a
  weather radar, RSS or Atom headlines, stock quotes, YouTube videos or an image slideshow and
  collage. The server fetches and caches the data, so API keys never reach a screen and screens
  keep showing the last data offline. Admins turn plugins on or off and set providers on the
  **Plugins** page. Some providers have terms for commercial use; see
  [docs/plugins.md](docs/plugins.md), and [docs/deployment.md](docs/deployment.md#plugins) for
  the hosts to allow through a firewall.

## Screens

1. On the screen, open `http://<server>:5000/player/` in a browser, in full screen or kiosk mode.
   It shows a six-character pairing code.
2. In the admin, go to **Displays > Pair a display**, type the code, name the screen and choose a
   default playlist. The screen starts playing within a few seconds.

Changes to playlists and media reach the screens within about a second. A screen keeps playing
what it has if the server goes offline, and catches up when the server is back. Use **Reload** on
a display to refresh its page, and **Unpair** to make it show a new pairing code.

Each display has its own options under **Edit**: location and notes, orientation (for screens
mounted on their side), one fit mode for every item, video volume, an "offline" banner, and,
for admins, player controls (touch, mouse, keyboard and remote pause, seek and skip; off by
default), the auto-resume time after a pause, an unlock PIN for the player apps and **Lock
player**, which keeps the Windows and Android apps from being closed without that PIN
(see [docs/kiosk-lockdown.md](docs/kiosk-lockdown.md)). A locked screen can be unlocked
remotely from its display page for a while. On the display page, **Identify** shows the name on the screen,
**Screenshot** shows what the screen is showing, and **Diagnostics** shows its screen size,
app version, cache and recent errors.

Each connected screen keeps one server thread busy. `flask --app wsgi serve` sizes the threads
from `AISIGNX_MAX_STREAMS` (default 50 screens), so raise that setting if you have more screens.
Run a single server process. For start-up and kiosk browser settings see
[docs/deployment.md](docs/deployment.md); for HTTPS and reverse proxies see
[docs/https.md](docs/https.md). The player API is described in
[docs/player-api.md](docs/player-api.md).

## Player apps

Instead of a browser, a screen can run one of the AiSignX player apps. They show the same
player and add a setup screen, offline start-up from a saved copy of the player, a media cache,
certificate pinning for self-signed HTTPS, kiosk mode with an optional unlock PIN, real-screen
screenshots and autostart.

- **Windows** (Electron): `clients/electron-client`, built with `npm run dist:win`.
- **Android** 8.0 or newer, including Android TV boxes: `clients/android-client`, built with
  `.\gradlew.bat assembleRelease`.

Start the app, enter the server address, press **Test**, then **Save and start**, and pair the
screen in the admin as usual. See [docs/clients.md](docs/clients.md) for setup, HTTPS, offline
behaviour, kiosk settings and build steps.

## Schedules, groups and emergencies

- **Default playlist:** what a screen plays when no schedule matches.
- **Schedules:** play a playlist on all screens, a group or one display, on chosen weekdays, all
  day or between two times (22:00-06:00 runs overnight), optionally between two dates. Higher
  priority wins; at equal priority a display schedule beats a group schedule, which beats one for
  all screens. Times use the time zone from **Settings**.
- **Groups:** put displays in a group (one group per display) to target them together.
- **Emergency:** send a message to all screens, a group or one display. It covers the screen (or
  shows as a banner over an emergency playlist) until someone clears it, even on a screen that
  loses its connection. Editors and admins can send and clear.

Screens switch within about a second when a schedule starts or ends. Details, including daylight
saving rules, are in [docs/scheduling.md](docs/scheduling.md).

## Operations

- **Backups:** a daily ZIP archive of the database, secret key and uploads (kept 7 by default),
  plus manual backups, download, upload and restore under **Admin > Backups**, or
  `flask --app wsgi backup create|list|verify|restore|prune`. Archives contain secrets; copy
  them off-site somewhere private. See [docs/backup.md](docs/backup.md).
- **Alerts:** screens offline, low disk space, bursts of failed sign-ins, failed or missing
  backups and failing background jobs show under **Alerts**, optionally by e-mail.
- **Audit log:** who changed what and when, with filters, CSV export and History links on
  edit pages (**Admin > Audit log**).
- **Sign-in protection:** failed sign-ins are limited per IP address and per user name.
- **API tokens and media API:** each user can create tokens under **API tokens** in their menu.
  The JSON media API lists, uploads and replaces media, and serves the PowerPoint add-in. See
  [docs/api.md](docs/api.md).
- **Proof of play:** when turned on, screens report what they played; **Reports > Plays** shows
  totals and exports CSV.
- **Health:** **Admin > System** runs a self-test, **Admin > Logs** shows the log file, and
  `/healthz` answers uptime monitors.

All of this runs inside the one server process. Details, retention and a monthly checklist are
in [docs/operations.md](docs/operations.md).

## Roles

- **Admin:** everything, including users, settings, plugin settings, backups, the audit log,
  the system page and all API tokens.
- **Editor:** media (including plugin items), playlists, schedules and displays; can
  acknowledge alerts and mute displays; API tokens with read and write access to media.
- **Viewer:** read-only, including alerts and play reports; API tokens that can only read media.

## Development

```powershell
cd server
.venv\Scripts\python -m pytest
.venv\Scripts\flask --app wsgi db migrate -m "describe change"   # after changing models

cd ..\clients\electron-client; npm ci; npm test                   # Electron client
cd ..\android-client; .\gradlew.bat testDebugUnitTest             # Android client
```

## License

AGPL-3.0. See [LICENSE](LICENSE).
