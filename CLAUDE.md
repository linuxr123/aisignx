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

## Platform scope (decided by the owner)
- **Windows and Android are primary.** Linux is best effort, with free means only (no paid certificates, no paid services). **macOS is dropped**: do not add or promise it. (`scripts/install-server.sh` still has a launchd branch from earlier work; it is unsupported.)
- Nothing built so far has run on real hardware beyond the owner's Galaxy Tab test and an NVIDIA Shield. In docs, say plainly when a behaviour is only unit-tested, and never claim a device confirmed it. `docs/testing-checklist.md` lists what still needs a device.

## Target layout
```
server/
  aisignx/            # package
    __init__.py       # create_app()
    config.py         # loads settings from env / .env
    extensions.py     # db, migrate, login_manager, csrf
    models/           # one file per area
    auth/  users/  settings/  setup/  main/  tokens/  api/
    media/  playlists/  plugins/  schedules/  groups/  emergency/
    displays/         # pairing, forms, device agent, signed commands (signing.py, agent.py, agent_admin.py,
                      #   enroll.py, ecies.py, service.py, screenshots.py)
    player/           # browser player and the player API (/api/player/v1)
    downloads/        # installers for the apps (Downloads page, `flask downloads add`)
    updates/          # update check and release-signature verification
    provision/        # Android QR provisioning and the install link (blueprint install_link, /get/<code>)
    alerts/  audit/  backup/  pop/  system/
    templates/  static/
  migrations/         # Alembic, 0001 to 0020
  tests/
  requirements.txt
  wsgi.py
clients/              # electron-client (Windows player + privileged agent), android-client, powerpoint-addin
scripts/  tools/      # install scripts; release-sign.py
docker/  Dockerfile  docker-compose.yml
docs/
```

## Phases (status)
1. **Core:** done.
2. **Content:** done (media, thumbnails, durations, playlists, web page screenshots, TV-friendly copies).
3. **Screens:** done (pairing, browser player, heartbeat, live push over SSE, synchronized playback).
4. **Scheduling:** done (schedules, groups, emergency override, saved and scheduled emergencies, emergency API).
5. **Clients:** done in code (Windows and Android player apps, device agent, signed commands, Windows privileged agent, Android QR provisioning and lockdown, PowerPoint add-in). Mostly **not verified on real hardware**.
6. **Operations:** done (backup and restore, alerts, audit log, proof of play, API tokens, Docker, HTTPS guide, server installers, Downloads page, update check).
7. **Later:** plugins are done (clock, weather, radar, RSS, stocks, YouTube, image library). Multiple sites and SSO are on hold.

Planned, not built: one-click server update (needs a privileged helper and a release key), recurring emergencies, API plan scheduling, a brightness setting, a live mode for web pages in the Windows app, media tags and folders, smart playlists. See the Planned list in `CHANGELOG.md`.

## v2 code worth reusing (strip tenancy)
- Player: `AISignXV2\server\static\js\display_player.js`, `server\display_player.py`
- Media: `server\media.py`, `server\media_duration.py`, `server\thumbnail_utils.py`, `server\upload_paths.py`
- Playlists and schedules: `server\playlists.py`, `server\schedules.py`, `server\schedule_conflicts.py`
- Pairing and tokens: `server\registration.py`, `server\tokens.py`, `server\heartbeat.py`
- Clients: `AISignXV2\clients\electron-client`, `AISignXV2\clients\android-client`

## Workflow
- After each change: run `pytest` (in `server`, with `.venv`), then boot the app (`flask --app wsgi serve` or `run`) and check that it starts.
- **Restart the server after any server change** before testing it by hand; a running server keeps the old code and the old templates.
- Prefer read-only checks (grep, `pytest --collect-only`, `git diff`, `flask --app wsgi check`) before changing anything. Do not edit `C:\Users\Linuxr\Desktop\AISignXV2`.
- Windows registry paths: backslashes must be escaped when a path passes through a string (JSON, JavaScript, Python, PowerShell here-strings). A path like `HKLM\SOFTWARE\...` that lost its backslashes silently goes to the wrong place. Test the exact string that reaches the registry tool.
- To debug a tablet or screen without a cable: Displays > the display > **Collect logs** (needs Edit > Device agent > Allow log collection), then download the log from the display page. Server logs: **Admin > Logs**. Use these first.
- Commit in small, focused steps with clear messages on branch `v3`. Ask before pushing.
- Keep `CHANGELOG.md`, the README and the docs up to date as features land. Admin menu names used in all docs: Dashboard, Media, Plugins, Playlists, Displays, Groups, Schedules, Emergency, Alerts, Reports, Downloads, Users, Settings, All API tokens, Audit log, Backups, Updates, System, Logs.
- The owner tests at the end: work through the roadmap without check-ins between items, but still ask before pushing.
- Run one workflow (parallel agents) at a time; parallel workflows burn the token budget quickly.
- The user prefers short, direct answers.
