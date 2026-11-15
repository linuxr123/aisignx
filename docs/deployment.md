# Deployment

How to run AiSignX for real screens: the server, start-up, plugin network access, the kiosk
browser, and pointers to [HTTPS](https.md) and [Docker](docker.md).
For a quick test at home, `flask --app wsgi run` is enough.

## Run the server

In the `server` folder:

```powershell
.venv\Scripts\flask --app wsgi serve        # Linux/macOS: .venv/bin/flask --app wsgi serve
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

**Linux (systemd).** Save as `/etc/systemd/system/aisignx.service`, then run
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

**Settings** (in `server/.env` or the service environment; restart after a change):

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
from the internet). Put a reverse proxy such as Caddy, nginx or IIS in front of AiSignX, then set
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
