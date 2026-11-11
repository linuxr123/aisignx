# AiSignX v3: guide for Claude Code

AiSignX is self-hosted digital signage. One web admin manages playlists, schedules and screens.
v3 is a **clean rewrite** as a **single-organization** app. It must run well at home, in a small business, or at a company with a few dozen screens. The author may sell it or offer support later, so code quality matters.

## Workspace
- `C:\Users\Linuxr\Desktop\AISignX3`: this folder, branch `v3`. All new work goes here.
- `C:\Users\Linuxr\Desktop\AISignXV2`: the old v2 code (multi-tenant), branch `main`. **Read it for reference only. Never edit it.**
- Remote: `https://github.com/linuxr123/aisignx` (public, AGPL-3.0).
- Platform: Windows with PowerShell. Python virtual environment in `server\.venv`.

## Hard rules
- **No multi-tenancy.** No `domain_id`, `tenant_id`, tenant filters or tenant switching. One organization, configured in Settings.
- Roles: `admin`, `editor` (media, playlists, schedules, displays), `viewer` (read-only).
- **No default passwords.** A first-run setup wizard creates the admin account. Never put real or sample credentials in tracked files.
- Never commit `.env`, `config.py`, `*.db`, `uploads/`, keystores or build output.
- Keep files small: aim for under ~800 lines, one feature per Flask blueprint.
- Copy code from v2 **one piece at a time**, and strip tenant logic as you go. Never copy whole modules.

## Stack
- Python 3.10+, Flask with blueprints and the app-factory pattern, Flask-SQLAlchemy, Flask-Login, Flask-WTF (CSRF).
- SQLite by default; any DB URL must work, so Postgres can be used later.
- **Alembic (Flask-Migrate) migrations from the first model.** Never use `db.create_all()` in production paths.
- Live updates to players: Server-Sent Events (SSE).
- Templates: Jinja2 + Bootstrap 5. No frontend build step.
- Tests: pytest. Every feature needs at least smoke tests.

## Target layout
```
server/
  aisignx/            # package
    __init__.py       # create_app()
    config.py         # loads settings from env / .env
    extensions.py     # db, migrate, login_manager, csrf
    models/           # one file per area
    auth/  users/  settings/  media/  playlists/  displays/  schedules/  player/
    templates/  static/
  migrations/
  tests/
  requirements.txt
  wsgi.py
clients/              # electron-client, android-client (adapted later from v2)
docs/
```

## Phases (finish and test each one before starting the next)
1. **Core:** app factory, config, Alembic, login/logout, users and roles, settings page, first-run setup wizard, base layout.
2. **Content:** media upload (image, video, web URL), thumbnails, durations, playlists with ordering and transitions.
3. **Screens:** pair a display with a short code, browser player at `/player`, heartbeat/online status, live push over SSE.
4. **Scheduling:** time and day-of-week schedules, display groups, emergency override.
5. **Clients:** adapt the v2 Electron and Android clients to the v3 pairing flow.
6. **Operations:** backup and restore, audit log, proof of play, Dockerfile, HTTPS guide.
7. **Later:** plugins (weather, RSS), multiple sites, SSO.

## v2 code worth reusing (strip tenancy)
- Player: `AISignXV2\server\static\js\display_player.js`, `server\display_player.py`
- Media: `server\media.py`, `server\media_duration.py`, `server\thumbnail_utils.py`, `server\upload_paths.py`
- Playlists and schedules: `server\playlists.py`, `server\schedules.py`, `server\schedule_conflicts.py`
- Pairing and tokens: `server\registration.py`, `server\tokens.py`, `server\heartbeat.py`
- Clients: `AISignXV2\clients\electron-client`, `AISignXV2\clients\android-client`

## Workflow
- After each change: run `pytest`, then boot the app (`flask --app wsgi run`) and check that it starts.
- Commit in small, focused steps with clear messages on branch `v3`. Ask before pushing.
- Keep `CHANGELOG.md` and the README up to date as features land.
- The user prefers short, direct answers.
