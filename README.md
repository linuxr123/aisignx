# AiSignX

Self-hosted digital signage. One web admin manages playlists, schedules and screens,
for a home, a small business, or a company with a few dozen screens.

> **Status:** v3 is a rewrite in progress. Phase 1 (accounts, roles, settings, setup wizard),
> Phase 2 (media library, playlists), Phase 3 (screens: pairing, browser player, live updates)
> Phase 4 (schedules, display groups, emergency override) and Phase 5 (Windows and Android player
> apps) are done. Operations (backup, audit log, Docker) are next. See [CHANGELOG.md](CHANGELOG.md).

## Requirements

- Python 3.10 or newer
- [ffmpeg](https://ffmpeg.org/): reads video length and resolution and makes video thumbnails.
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

To check the system at any time, run `flask --app wsgi check` in `server/`. Admins also see a
warning on the dashboard if a required program is missing.

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

## Media and playlists

- **Media:** upload images (JPG, PNG, GIF, WebP, BMP) and videos (MP4, WebM, MOV, M4V, MKV), or
  add a web page by URL. Files are checked on upload and stored under random names.
- **Web page previews:** a headless browser takes the screenshot in the background, so it appears
  a few seconds after you add the page. Use **Refresh preview** on the edit page when the site
  changes. If the page can't be loaded, a title card is shown instead.
- **Playlists:** drag items to reorder them. Each item can override its duration, transition,
  fit mode (fit, fill or stretch) and, for videos, mute. Videos play for their full length unless
  you set a duration.

## Screens

1. On the screen, open `http://<server>:5000/player/` in a browser, in full screen or kiosk mode.
   It shows a six-character pairing code.
2. In the admin, go to **Displays > Pair a display**, type the code, name the screen and choose a
   default playlist. The screen starts playing within a few seconds.

Changes to playlists and media reach the screens within about a second. A screen keeps playing
what it has if the server goes offline, and catches up when the server is back. Use **Reload** on
a display to refresh its page, and **Unpair** to make it show a new pairing code.

Each connected screen keeps one server thread busy. `flask --app wsgi serve` sizes the threads
from `AISIGNX_MAX_STREAMS` (default 50 screens), so raise that setting if you have more screens.
Run a single server process. For kiosk browser settings, reverse proxies and HTTPS, see
[docs/deployment.md](docs/deployment.md). The player API is described in
[docs/player-api.md](docs/player-api.md).

## Player apps

Instead of a browser, a screen can run one of the AiSignX player apps. They show the same
player and add a setup screen, offline start-up from a saved copy of the player, a media cache,
certificate pinning for self-signed HTTPS, kiosk mode and autostart.

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

## Roles

- **Admin:** everything, including users and settings.
- **Editor:** media, playlists, schedules and displays.
- **Viewer:** read-only.

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
