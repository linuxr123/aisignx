# Deployment

How to run AiSignX for real screens: the server, start-up, the kiosk browser, and pointers to
[HTTPS](https.md) and [Docker](docker.md).
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
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --kiosk --autoplay-policy=no-user-gesture-required --user-data-dir=C:\AiSignX\kiosk-profile --no-first-run http://192.168.1.10:5000/player/
```

Linux (Chromium):

```bash
chromium --kiosk --autoplay-policy=no-user-gesture-required --user-data-dir="$HOME/.aisignx-kiosk" --no-first-run http://192.168.1.10:5000/player/
```

With Microsoft Edge, use `--start-fullscreen` instead of `--kiosk`, with the other flags as above.
Edge's own kiosk mode browses InPrivate and forgets the pairing at every start.

Put the command in the screen computer's start-up (the Windows Startup folder or Task Scheduler,
or your Linux desktop's autostart), and turn off screen sleep and screen savers.

To troubleshoot a screen, open `/player/?debug=1`. It shows the player state, content version,
live-update status and the last heartbeat.
