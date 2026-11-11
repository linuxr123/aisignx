# AiSignX

Self-hosted digital signage. One web admin manages playlists, schedules and screens,
for a home, a small business, or a company with a few dozen screens.

> **Status:** v3 is a rewrite in progress. Phase 1 (accounts, roles, settings, setup wizard) and
> Phase 2 (media library, playlists) are done. Displays and scheduling are coming next.
> See [CHANGELOG.md](CHANGELOG.md).

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
.venv\Scripts\flask --app wsgi run        # Linux/macOS: .venv/bin/flask --app wsgi run
```

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

## Media and playlists

- **Media:** upload images (JPG, PNG, GIF, WebP, BMP) and videos (MP4, WebM, MOV, M4V, MKV), or
  add a web page by URL. Files are checked on upload and stored under random names.
- **Web page previews:** a headless browser takes the screenshot in the background, so it appears
  a few seconds after you add the page. Use **Refresh preview** on the edit page when the site
  changes. If the page can't be loaded, a title card is shown instead.
- **Playlists:** drag items to reorder them. Each item can override its duration, transition,
  fit mode (fit, fill or stretch) and, for videos, mute. Videos play for their full length unless
  you set a duration.

## Roles

- **Admin:** everything, including users and settings.
- **Editor:** media, playlists, schedules and displays.
- **Viewer:** read-only.

## Development

```powershell
cd server
.venv\Scripts\python -m pytest
.venv\Scripts\flask --app wsgi db migrate -m "describe change"   # after changing models
```

## License

AGPL-3.0. See [LICENSE](LICENSE).
