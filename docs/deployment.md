# Deployment

How to run AiSignX for real screens: the server, a reverse proxy, HTTPS and the kiosk browser.
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
from the internet). The simplest way is a reverse proxy in front of AiSignX:

1. Start AiSignX on the local machine only: `flask --app wsgi serve --host 127.0.0.1`.
2. Point the proxy at `http://127.0.0.1:5000`.
3. In `server/.env`, set:
   - `AISIGNX_PROXY_HOPS=1`, so AiSignX reads the real client IP and `https` from the proxy's
     `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host` headers. Use `2` with two
     proxies in a row. Leave it at `0` without a proxy, or anyone could fake those headers.
   - `AISIGNX_SECURE_COOKIES=1`, so login and screen cookies are only sent over HTTPS. With this
     on, signing in over plain `http://` no longer works.
4. Restart AiSignX and open the screens at `https://<your-host>/player/`.

The live-update stream (`/api/player/v1/events`) must not be buffered or compressed by the
proxy, and the proxy must allow at least 75 seconds without data (the server sends a
keep-alive every 20 seconds).

**Caddy** needs nothing extra. It gets a certificate automatically and streams events as they
come:

```
signage.example.com {
    reverse_proxy 127.0.0.1:5000
}
```

**nginx:**

```nginx
server {
    listen 443 ssl;
    server_name signage.example.com;
    # ssl_certificate ... ; ssl_certificate_key ... ;
    client_max_body_size 2048m;            # match AISIGNX_MAX_UPLOAD_MB

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
    }

    location /api/player/v1/events {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 120s;
        gzip off;
    }
}
```

**IIS with Application Request Routing (ARR):**

- In **Server Proxy Settings**, set **Response buffer threshold** to `0` and the time-out to at
  least 75 seconds.
- Turn off dynamic compression for the site, or at least for `text/event-stream`.
- ARR sends `X-Forwarded-For` by default. Add a URL Rewrite server variable so it also sends
  `X-Forwarded-Proto: https`.

The [player apps](clients.md) never skip certificate checks. With a self-signed certificate,
install your own CA on the devices or pin the certificate in the app's setup. To compare the
fingerprint the app shows, print it on the server:

```bash
openssl x509 -noout -fingerprint -sha256 -in cert.pem
```

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
