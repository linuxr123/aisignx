# Deployment

How to run AiSignX for real screens: the server, settings, where data lives, start-up, plugin
network access, the kiosk browser, and pointers to [HTTPS](https.md) and [Docker](docker.md).
For a quick test at home, `flask --app wsgi run` is enough. To install the server as a service,
see [installing-the-server.md](installing-the-server.md).

Platforms: Windows and Linux are supported; Docker runs anywhere Docker does. macOS is not
supported. None of this has been run on a large installation or on a wide range of hardware yet.

## Run the server

In the `server` folder:

```powershell
.venv\Scripts\flask --app wsgi serve        # Linux: .venv/bin/flask --app wsgi serve
```

Options: `--host` (default `0.0.0.0`, all network interfaces), `--port` (default `5000`) and
`--threads`. `serve` runs the waitress web server, which works on Windows and Linux.

**Threads.** Every screen keeps one live-update connection open, and each open connection keeps
one server thread busy. `serve` therefore starts `AISIGNX_MAX_STREAMS + 16` threads (66 with the
default of 50 screens), so the admin pages stay fast. If you have more than 50 screens, set
`AISIGNX_MAX_STREAMS` in `server/.env` to the number of screens plus a few spare. Screens above
the limit still work: they get updates through their 30-second heartbeat instead of instantly.

Don't start the app with a bare `waitress-serve wsgi:app`. It uses only 4 threads, and the
admin stops responding once 4 screens are connected.

**One process only.** Run a single server process (with many threads). Live updates and the
**Reload** button only reach screens connected to the same process. If you run several
processes or workers anyway, screens still pick up changes, but only within about 30 seconds.

**Database.** SQLite runs in WAL mode. Keep `server/instance` on a local disk, never on a
network share.

## Settings and data folders

Settings are environment variables. Put them in `server/.env` (read at start-up; the service
installers write `aisignx.env` instead, see [installing-the-server.md](installing-the-server.md)) or in the
service environment, and restart after a change. Nothing here is required: AiSignX runs with
none of them set.

| Variable | Default | Meaning |
|---|---|---|
| `AISIGNX_SECRET_KEY` | generated | Session signing key. Unset: a random key is made once and kept in `instance/secret_key`. It also encrypts API keys saved on the Plugins page. |
| `AISIGNX_DATABASE_URL` | SQLite file `instance/aisignx.db` | Any SQLAlchemy URL. With PostgreSQL see [backup.md](backup.md#postgresql). |
| `AISIGNX_INSTANCE_DIR` | `server/instance` | Folder for the database, `secret_key` and `command_key`. |
| `AISIGNX_UPLOAD_DIR` | `uploads` beside `instance/` | Uploaded media, thumbnails, screenshots and device logs. |
| `AISIGNX_BACKUP_DIR` | `backups` beside `instance/` | Backup archives. |
| `AISIGNX_LOG_DIR` | `logs` beside `instance/` | `aisignx.log`, rotated at 5 MB, 5 files. |
| `AISIGNX_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR`. |
| `AISIGNX_SECURE_COOKIES` | off | `1` marks cookies Secure. Set it once the admin is only used over HTTPS ([https.md](https.md)). |
| `AISIGNX_PROXY_HOPS` | `0` | Number of reverse proxies in front whose `X-Forwarded-*` headers are trusted. |
| `AISIGNX_MAX_UPLOAD_MB` | `2048` | Largest upload (media, Downloads files, backup archives). |
| `AISIGNX_MAX_STREAMS` | `50` | Open live-update connections. `serve` starts this plus 16 threads. |
| `AISIGNX_SCHEDULE_TICKER` | on | `0` turns off the per-minute push of schedule changes ([scheduling.md](scheduling.md)). |
| `AISIGNX_JOBS` | on | `0` turns off the background jobs (alerts, backups, retention, proof-of-play totals, scheduled emergencies, update check). |
| `AISIGNX_FFMPEG`, `AISIGNX_FFPROBE` | found on `PATH` | Paths to ffmpeg and ffprobe (video length, thumbnails, conversion). |
| `AISIGNX_BROWSER` | auto-detect | Edge, Chrome or Chromium used for web page previews. |
| `AISIGNX_BROWSER_NO_SANDBOX` | off | `1` starts that browser with `--no-sandbox` (needed in a container without root). |
| `AISIGNX_WEBSHOT_TIMEOUT` | `45` | Seconds allowed per web page screenshot. |
| `AISIGNX_SMTP_PASSWORD` | none | Password for alert e-mail; never stored in the database ([operations.md](operations.md#e-mail)). |
| `AISIGNX_LOGIN_MAX_PER_IP` | `20` | Failed sign-ins (and failed API token checks) per IP address per window. |
| `AISIGNX_LOGIN_MAX_PER_USER` | `5` | Failed sign-ins per user name per window. |
| `AISIGNX_LOGIN_WINDOW_MIN` | `15` | Length of that window in minutes. |
| `AISIGNX_UPDATE_CHECK` | on | `0` (also `false`, `no`, `off`) switches the daily check for a newer release off, whatever the Updates page says ([updates.md](updates.md)). |
| `AISIGNX_UPDATE_REPO` | `linuxr123/aisignx` | `owner/name` of the GitHub repository the update check asks. A malformed value falls back to the default. |
| `AISIGNX_PLUGIN_CACHE_DIR`, `AISIGNX_PLUGIN_CACHE_MB`, `AISIGNX_PLUGIN_HTTP_PROXY`, `AISIGNX_OPENMETEO_KEY`, `AISIGNX_FINNHUB_KEY` | see [Plugins](#plugins) | Plugin data cache, its size cap, the plugin proxy and provider keys. |

The folders `uploads`, `backups`, `logs` and `plugin-cache` are placed next to the instance
folder. That parent is the **data folder**: `server/` in a plain checkout,
`C:\ProgramData\AiSignX` or `/var/lib/aisignx` with the installers, `/data` in Docker. It holds:

| Where | Contents |
|---|---|
| `instance/aisignx.db` | The SQLite database (with `-wal` and `-shm` files while running). |
| `instance/secret_key` | The session key, unless `AISIGNX_SECRET_KEY` is set. |
| `instance/command_key` | The server's Ed25519 key that signs reboot, startup and update commands to screens. Made the first time a screen or the Android provisioning page needs it. Owner-only. Screens pin its public key on first contact, so keep it: a new server with a new key makes screens refuse those commands. The fingerprint is on **Admin > System**. Included in every backup and restored with it ([backup.md](backup.md)). |
| `uploads/` | `media`, `thumbs`, `screenshots`, `agent-logs` (device logs collected from screens) and `tmp`. |
| `downloads/` | The installers on the **Downloads** page, stored under random names ([downloads.md](downloads.md)). **Not part of a backup.** Installers pushed to screens are read from here. |
| `backups/`, `logs/`, `plugin-cache/` | Backup archives, application log, fetched plugin data. |

Never serve `uploads` or `downloads` straight from a web server; AiSignX checks the user or the
screen's token before it hands out a file.

**Firewall.** Screens connect to port 5000. On Windows, allow it once in an admin PowerShell:

```powershell
New-NetFirewallRule -DisplayName "AiSignX" -Direction Inbound -Protocol TCP -LocalPort 5000 -Action Allow
```

## Start automatically

**Windows.** Create a Task Scheduler task that runs at system start-up, whether or not a user is
signed in:

- Program: `C:\path\to\AISignX3\server\.venv\Scripts\flask.exe`
- Arguments: `--app wsgi serve`
- Start in: `C:\path\to\AISignX3\server`

A service wrapper such as NSSM works too, with the same three values.

**Linux (systemd).** The installer script does this for you
([installing-the-server.md](installing-the-server.md)). By hand, save as `/etc/systemd/system/aisignx.service`, then run
`sudo systemctl enable --now aisignx`:

```ini
[Unit]
Description=AiSignX digital signage
After=network-online.target

[Service]
User=aisignx
WorkingDirectory=/opt/aisignx/server
ExecStart=/opt/aisignx/server/.venv/bin/flask --app wsgi serve
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Plugins

Plugins (weather, radar, RSS, stocks; see [plugins.md](plugins.md)) make outbound requests from
the **server**. Screens never contact these providers, except YouTube, which each screen loads
itself.

**Plugin settings** (same places as above):

| Variable | Default | Meaning |
|---|---|---|
| `AISIGNX_PLUGIN_CACHE_DIR` | `plugin-cache` beside `instance/` | Where fetched plugin data is kept. Never backed up; it is fetched again after a restore. |
| `AISIGNX_PLUGIN_CACHE_MB` | `500` | Size cap of that folder. Data nobody read for 7 days is removed daily. |
| `AISIGNX_PLUGIN_HTTP_PROXY` | none | `http://host:port` of an HTTPS (CONNECT) proxy for plugin requests. Other proxy variables (`HTTPS_PROXY`, …) are ignored. Behind a proxy, the check against private addresses can only use local DNS answers. |
| `AISIGNX_OPENMETEO_KEY` | none | Open-Meteo API key. Needed for commercial use of Open-Meteo weather and place search. |
| `AISIGNX_FINNHUB_KEY` | none | Finnhub API key. The stocks plugin shows nothing without it. |

Run one server process: the plugin cache, its rate limits and its background fetches are per
process.

**Outbound hosts** for a firewall allowlist. All use HTTPS on port 443 unless noted.

| Plugin | Hosts |
|---|---|
| Weather | `api.open-meteo.com` (or `customer-api.open-meteo.com` with a key), or `api.met.no` |
| Place search (weather, radar forms) | `geocoding-api.open-meteo.com` (or `customer-geocoding-api.open-meteo.com` with a key), `nominatim.openstreetmap.org` |
| Weather radar | `api.rainviewer.com`, `tilecache.rainviewer.com` or `mesonet.agron.iastate.edu`; `tile.openstreetmap.org` (or your own tile server); `api.weather.gov` (US alerts) |
| RSS / Atom | The feed hosts you enter, and the hosts of their images (HTTP or HTTPS, ports 80, 443, 8080 or 8443) |
| Stocks | `finnhub.io` |
| YouTube | From each **screen**, not the server: `www.youtube-nocookie.com` and YouTube's video and image hosts (`*.googlevideo.com`, `i.ytimg.com`, `*.ytimg.com`, `*.ggpht.com`, `www.google.com`) |
| Clock, image library | none |

**User-Agent.** Requests send `AiSignX/<version> (+https://github.com/linuxr123/aisignx)`.
The admin can add a contact e-mail or URL on the plugins page; MET Norway requires one.

**Terms and licences.** Choose providers that fit your use:

| Provider | Terms in short |
|---|---|
| Open-Meteo | CC BY 4.0. Free for non-commercial use; commercial use needs a paid API key. |
| MET Norway | CC BY 4.0 and NLOD, free including commercial use. Requires a contact in the User-Agent and respectful caching (AiSignX honours `Expires`). |
| OpenStreetMap tiles | Light use only under the tile usage policy, attribution required. Busy or commercial deployments should use their own or a commercial tile server. Nominatim: at most one request per second (AiSignX enforces this). |
| RainViewer | Free API with past radar frames and attribution; check its current limits before commercial use. |
| Iowa Environmental Mesonet | Free US NEXRAD tiles. |
| api.weather.gov | US government data, public domain. |
| Finnhub | The free plan is for personal, non-commercial use. Showing quotes on public or business screens may need a paid plan and exchange display licences. |
| YouTube | YouTube's Terms of Service apply; embeds may show ads; public playback rights are the operator's responsibility. |
| RSS feeds | The publisher's terms apply to showing their content on public screens. |

## Reverse proxy and HTTPS

Use HTTPS when the network is not fully trusted (guest Wi-Fi, shared offices, anything reachable
from the internet). It matters more now that screens can be told to reboot or install an app:
the first time a screen sees the server's command key over plain `http://` it cannot tell a
fake server from the real one, and API tokens and admin sign-ins cross the network in the
clear. Sending a Windows password for automatic sign-in is refused over plain `http://` (except
from the server machine itself). Put a reverse proxy such as Caddy, nginx or IIS in front of AiSignX, then set
`AISIGNX_PROXY_HOPS=1` and `AISIGNX_SECURE_COOKIES=1`. The proxy must not buffer the screens'
live-update stream. [https.md](https.md) has the full steps and examples for Caddy, nginx, IIS
and Cloudflare, and how screens and the PowerPoint add-in trust a private certificate.

## Docker

A `Dockerfile` and `docker-compose.yml` are in the repository root, with an optional Caddy
service for HTTPS. See [docker.md](docker.md).

## Player apps

For screens that run unattended, the Windows (Electron) and Android player apps add offline
start-up, a media cache, certificate pinning, kiosk mode and autostart around the same player.
See [clients.md](clients.md) for installing, setting up and building them.

## Kiosk browser on the screen

Any current Chrome, Chromium or Edge can be the player. Open `http://<server>:5000/player/`,
pair it once, and it keeps playing after restarts.

- **Use a persistent browser profile.** The screen remembers its pairing in a browser cookie.
  Incognito or InPrivate windows, guest profiles and "clear cookies on exit" make the screen ask
  for a new pairing code at every start. Give the kiosk its own `--user-data-dir`.
- **Allow sound.** Browsers block videos with sound until someone clicks. Start the browser with
  `--autoplay-policy=no-user-gesture-required`. Without it, AiSignX plays videos muted.

Windows (Chrome):

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --noerrdialogs --disable-session-crashed-bubble --disable-pinch --overscroll-history-navigation=0 --disable-features=Translate --autoplay-policy=no-user-gesture-required --no-first-run --user-data-dir=C:\AiSignX\kiosk-profile http://192.168.1.10:5000/player/
```

Linux (Chromium):

```bash
chromium --kiosk --noerrdialogs --disable-session-crashed-bubble --disable-pinch --overscroll-history-navigation=0 --disable-features=Translate --autoplay-policy=no-user-gesture-required --no-first-run --user-data-dir="$HOME/.aisignx-kiosk" http://192.168.1.10:5000/player/
```

With Microsoft Edge, use `--start-fullscreen` instead of `--kiosk`, with the other flags as above.
Edge's own kiosk mode browses InPrivate and forgets the pairing at every start.

The extra flags hide crash and restore bubbles, error dialogs and the translate bar, and turn
off pinch zoom and swipe-back navigation, so a touch screen stays on the player.

**A browser cannot be locked.** Anyone with a keyboard can leave it (Alt+F4, Alt+Tab, the
Windows key). The display's **Lock player** option only works in the Windows and Android apps.
For a locked screen, use the Windows app, or Windows Assigned Access / Shell Launcher; see
[kiosk-lockdown.md](kiosk-lockdown.md).

With **Player controls** on, media keys in Chrome go to the page's Media Session, which the
player handles (play, pause, next, previous, seek).

Put the command in the screen computer's start-up (the Windows Startup folder or Task Scheduler,
or your Linux desktop's autostart), and turn off screen sleep and screen savers.

To troubleshoot a screen, open `/player/?debug=1`. It shows the player state, content version,
live-update status and the last heartbeat.
