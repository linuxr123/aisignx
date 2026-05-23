# AISignX server

Flask application: admin UI, REST API, scheduling, media storage, plugins, and display SSE push.

## Run locally

```bash
# First time (from this server/ directory)
python -m venv .venv
source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
python generate_config.py
python migration.py
python app.py
```

Or use `install_windows.ps1` / `install_linux.sh` in this folder.

## Layout

| Path | Purpose |
|------|---------|
| `app.py` | Application entry point |
| `templates/`, `static/` | Web UI and player assets |
| `plugins/` | Built-in and custom slide plugins |
| `uploads/` | Tenant media files (runtime; override via `disk.upload_root` or per-tenant path) |
| `upload_paths.py` | Upload root resolution, validation, migration |
| `docs/` | Documentation |
| `migrations/` | Schema migration helpers |

Display client sources are in the sibling **`../clients/`** directory.

## Docker

```bash
docker compose up -d --build
```

Run from this `server/` directory. See `docker-compose.yml`.
