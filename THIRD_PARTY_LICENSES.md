# Third-party licenses

AISignX is licensed under **AGPL-3.0-or-later** (see [LICENSE](LICENSE)).
The projects below are **dependencies** used by AISignX. Their licenses apply
to those components only, not to AISignX as a whole.

This list is a summary for convenience, not legal advice. See each project’s
repository for the authoritative license text.

## Server (Python)

| Component | License | Notes |
|-----------|---------|--------|
| [Flask](https://github.com/pallets/flask) | BSD-3-Clause | Web framework |
| [Flask-SQLAlchemy](https://github.com/pallets-eco/flask-sqlalchemy) | BSD-3-Clause | ORM integration |
| [Flask-Migrate](https://github.com/miguelgrinberg/flask-migrate) | MIT | Database migrations |
| [Flask-Login](https://github.com/maxcountryman/flask-login) | MIT | Session auth |
| [Werkzeug](https://github.com/pallets/werkzeug) | BSD-3-Clause | WSGI utilities |
| [SQLAlchemy](https://github.com/sqlalchemy/sqlalchemy) | MIT | Database toolkit |
| [moviepy](https://github.com/Zulko/moviepy) | MIT | Video processing |
| [ffmpeg-python](https://github.com/kkroening/ffmpeg-python) | Apache-2.0 | FFmpeg bindings |
| [Playwright](https://github.com/microsoft/playwright-python) | Apache-2.0 | Headless browser (thumbnails) |
| [Pillow](https://github.com/python-pillow/Pillow) | HPND | Image processing |
| [flask-cors](https://github.com/corydolphin/flask-cors) | MIT | CORS support |
| [requests](https://github.com/psf/requests) | Apache-2.0 | HTTP client |

**System dependency:** [FFmpeg](https://ffmpeg.org/) — LGPL/GPL depending on build; install separately on the host OS.

## Electron client (Node.js)

| Component | License | Notes |
|-----------|---------|--------|
| [Electron](https://github.com/electron/electron) | MIT | Desktop shell (bundles Chromium) |
| [electron-builder](https://github.com/electron-userland/electron-builder) | MIT | Installer packaging |

Electron distributions include **Chromium** and other third-party components under
their own licenses. See Electron’s `LICENSE` / `LICENSES.chromium.html` in a
built app for the full notice.

## Android client

| Component | License | Notes |
|-----------|---------|--------|
| [AndroidX / Material](https://developer.android.com/jetpack/androidx) | Apache-2.0 | UI libraries |
| [OkHttp](https://github.com/square/okhttp) | Apache-2.0 | HTTP client |
| [Kotlin coroutines](https://github.com/Kotlin/kotlinx.coroutines) | Apache-2.0 | Async |

## Plugins

Plugins under `server/plugins/` may be authored separately. Third-party plugins
distributed with or linked to AISignX may need to comply with AGPL when combined
with this software — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Obtaining source

Source for AISignX itself is this repository. For dependency source, use the
links above or your package manager’s source packages (`pip`, `npm`, Gradle).

When we publish binary releases (`.exe`, `.apk`, etc.), the corresponding
source is available at the Git tag matching that release on GitHub.
