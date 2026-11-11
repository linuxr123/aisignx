# AiSignX

Self-hosted digital signage. One web admin manages playlists, schedules and screens,
for a home, a small business, or a company with a few dozen screens.

> **Status:** v3 is a rewrite in progress. Phase 1 (core: accounts, roles, settings, setup wizard) is done.
> Media, playlists, displays and scheduling are coming next. See [CHANGELOG.md](CHANGELOG.md).

## Quick start (Windows, PowerShell)

```powershell
cd server
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\flask --app wsgi db upgrade     # create / update the database
.venv\Scripts\flask --app wsgi run
```

On Linux or macOS, use `.venv/bin/` instead of `.venv\Scripts\`.

Open http://127.0.0.1:5000. On first start, the setup wizard asks for your organization name,
time zone and the first admin account. There are no default passwords.

## Configuration

Settings come from environment variables or `server/.env` (see `server/.env.example`):

| Variable | Default | Purpose |
|---|---|---|
| `AISIGNX_SECRET_KEY` | generated in `server/instance/secret_key` | Session signing key |
| `AISIGNX_DATABASE_URL` | SQLite in `server/instance/aisignx.db` | Any SQLAlchemy URL (e.g. Postgres) |
| `AISIGNX_SECURE_COOKIES` | `0` | Set to `1` when serving over HTTPS |

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
