# Contributing to AISignX

Thank you for improving AISignX. This document explains how the repo is organized for contributors and what to check before opening a pull request.

## Development setup

1. Clone the repository.
2. Copy `server/config.example.py` to `server/config.py` (or run `python server/generate_config.py`).
3. Run the install script for your OS from the repo root (see [README.md](README.md)).
4. Start the server from `server/` with `python app.py`.

Your local folder may contain databases, uploads, virtual environments, and built client installers. **That is expected.** `.gitignore` excludes runtime and build artifacts so Git only tracks source and documentation.

## Repository boundaries

| Track in Git | Do not track |
|--------------|--------------|
| Python/JS/Kotlin source | `__pycache__`, `.venv`, `node_modules` |
| Templates, static assets (except built installers) | `server/static/clients/*.exe`, `*.apk`, etc. |
| `config.example.py`, `.env.example` | `server/config.py`, `.env` |
| Docs under `server/docs/` | `RESUME_CONTEXT.md`, logs, `*.db` |
| Build scripts at repo root | Android keystores, `keystore.properties` |

## Building clients locally

From the repo root:

- Windows: `build_clients_windows.ps1`
- Linux: `build_clients_linux.sh`

Use `-Help` / `--help` for selective builds (`-Electron`, `-Android`, `-NoBump`, etc.).

## Before you push

```bash
git status
```

Confirm you are **not** staging:

- `server/config.py` or any `.env` file with real values
- Keystores (`.keystore`, `keystore.properties`)
- Databases, uploads, or log files
- `node_modules/`, `dist/`, or compiled installers

If unsure, run:

```bash
git check-ignore -v path/to/suspicious/file
```

## Code style

- Match existing patterns in the file you edit.
- Keep admin UI list pages consistent (filters, bulk actions, modals) per project conventions.
- Enforce tenant boundaries server-side; UI filters are convenience only.

## Documentation

Update `server/docs/` when behavior, API routes, or setup steps change. Keep path references aligned with the `server/` + `clients/` layout.

**Index:** [`docs/README.md`](docs/README.md) at the repo root lists every guide.

## Pull requests

- Describe what changed and why.
- Note manual test steps (server start, affected UI, client build if relevant).
- Do not include secrets or large binary artifacts.

## Licensing

By contributing code, documentation, or other materials to this repository, you
agree that your contributions are licensed under the same terms as the project:
**GNU Affero General Public License v3.0 or later** (see [LICENSE](LICENSE)).

If you submit a **plugin** or extension intended to run inside AISignX, note that
combined distribution with the server may require that plugin’s source be
available under AGPL-compatible terms when you distribute the combined work.
