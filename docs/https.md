# HTTPS and reverse proxies

AiSignX itself speaks plain HTTP. For HTTPS, put a reverse proxy in front of it. Use HTTPS when
the network is not fully trusted: guest Wi-Fi, shared offices, or anything reachable from the
internet.

## Quick set-up with nginx (recommended)

The server comes with everything to put **nginx** in front of it with HTTPS. nginx is free and runs on Windows and Linux.

1. Install the AiSignX server first (`scripts\install-server.ps1`, `scripts/install-server.sh`).
2. Run the nginx script. It needs administrator rights (Windows asks; on Linux use `sudo`):

   | You have | Windows (PowerShell) | Linux |
   |---|---|---|
   | Nothing yet (a home or office LAN) | `scripts\setup-nginx.ps1 -Hostname signs.lan,192.168.1.19` | `sudo scripts/setup-nginx.sh --host signs.lan --host 192.168.1.19` |
   | A certificate you bought | `scripts\setup-nginx.ps1 -Cert C:\certs\fullchain.pem -Key C:\certs\key.pem` | `sudo scripts/setup-nginx.sh --cert fullchain.pem --key key.pem` |
   | A public name, for a free Let's Encrypt certificate | (use the Linux box, or a certificate client of your choice and `-Cert`/`-Key`) | `sudo scripts/setup-nginx.sh --letsencrypt signs.example.com --email you@example.com` |

   Without a certificate it makes a **self-signed** one for the names you give (3 years, ECDSA P-256).
3. Open `https://<name>/`. Done: the script turned on `AISIGNX_PROXY_HOPS=1` and `AISIGNX_SECURE_COOKIES=1`, restarted AiSignX, started nginx with the
   machine, opened ports 80 and 443 and closed the plain AiSignX port to other computers.

**Admin > System > HTTPS** shows the certificate (names, expiry, SHA-256), makes a new self-signed one, checks and stores
one you bought (PEM certificate chain and key; a key with a passphrase is accepted and stored without it for nginx), and
downloads the `nginx.conf` if you run nginx your own way. When the certificate changes there, **nginx reloads it by itself**
(Windows: within 5 minutes, a scheduled task; Linux: a systemd path unit).
The files are in `ProgramData\AiSignX\tls` (Windows) or `/var/lib/aisignx/tls`: `server.crt` (certificate and chain) and
`server.key`. **They are not part of backups**: keep your own copy of a certificate you bought. A `.pfx` file must be converted
once: `openssl pkcs12 -in my.pfx -out all.pem -nodes`, then split the certificate(s) and the key into two files.

**Updating nginx**

- Windows: `scripts\setup-nginx.ps1 -Update` looks at nginx.org, and when the **stable** version is newer it installs it,
  keeps your certificate and settings, checks the configuration with the new nginx, and **goes back to the old nginx by
  itself** if the new one does not start (the old one stays in `ProgramData\AiSignX\nginx\app.old`). `-AutoUpdate` (with
  the install) also registers a weekly task (Sunday 03:00) that does this unattended. The download is protected by HTTPS to
  nginx.org only: nginx.org publishes PGP signatures but no checksums, and Windows cannot check PGP by itself. The SHA-256 of
  what was installed is kept in `nginx\installed.json`. Leave `-AutoUpdate` off if you would rather update by hand.
- Linux: nginx comes from your distribution, so security updates arrive with the system's own updates;
  `sudo scripts/setup-nginx.sh --update` upgrades the package now and checks the configuration.

**Self-signed certificates.** Browsers warn until the certificate is trusted: install `server.crt` (download it on the HTTPS page) as a
trusted certificate on your PCs and phones. The Android and Windows player apps can **pin** a self-signed certificate instead
(see [clients.md](clients.md#https-and-certificate-pinning)); compare the SHA-256 shown on the page with what the app shows. Do
not turn on HSTS with a self-signed certificate (AiSignX refuses to write it): browsers would no longer let you click through.
The one-script Windows set-up (**Downloads > Set up a Windows PC**) downloads over the address you give it: with a self-signed
certificate that PC must trust it first, or use the plain `http://` address on a trusted LAN for that one step.

**What has and has not been tested.** The certificate code, the configuration text, the checks and the admin page are unit tested.
The two scripts are tested for syntax and their dry-run output, and the nginx.org download page parsing was checked against the live page.
The generated configuration was run with the real **nginx 1.30.7 for Windows**: `nginx -t` accepts it, and with a self-signed certificate nginx
answered `https://` pages from a running AiSignX, redirected port 80 to https, and passed the live-update path through (401 without a login, as it should).
The scripts themselves have **not** been run on a real PC or server (`docs/testing-checklist.md`, section N). Linux is best effort.

## Why HTTPS matters

On plain HTTP anyone on the network path can read what is sent. With the device features that
now exist, that is more than a sign-in:

- **Tokens and sign-ins.** Admin sessions, API tokens (`Authorization: Bearer`) and each screen's
  display token cross the network in the clear. A display token can fetch that screen's content and
  pending commands; an API token can upload media or raise emergencies.
- **Signed commands.** Reboot, startup changes and app updates are signed by the server's command
  key ([signed-commands.md](signed-commands.md)). A screen pins that key the first time it sees it
  (trust on first use). Over plain HTTP, someone who sits between the screen and the server at that
  moment can hand it their own key and later send it commands it will obey. Over HTTPS with a
  certificate the screen trusts, that window is closed. Screens set up with the Android QR code get
  the key inside the code, and the privileged Windows agent compares the key's fingerprint with the
  one the admin typed, so those two paths do not depend on the first contact. Compare the
  fingerprint on **Admin > System** with the one a screen shows if you want to check by hand.
- **Automatic Windows sign-in.** The form on the display page sends a Windows password to the server
  once, encrypted for the screen's agent. The server refuses it over plain `http://`, except when you
  browse from the server machine itself.
- **Installers.** An app update pushed to a screen is checked against the size and SHA-256 inside the
  signed command (and, by the privileged Windows agent, against the installer's Authenticode signature),
  but the download itself should not travel in the clear either.

For a small network you fully control, HTTP is workable. Anything shared, wireless for guests, or reachable from the internet
should use HTTPS. None of this has been tested on a hostile network.

This page covers the quick nginx set-up above, and by hand: Caddy, nginx, IIS and Cloudflare. For Docker, the compose file has a ready-made
Caddy profile (see [docker.md](docker.md#https-with-caddy)).

## The three steps for every proxy

1. Make AiSignX listen on the local machine only, so nobody can bypass the proxy:
   `flask --app wsgi serve --host 127.0.0.1`. In Docker, remove the `5000:5000` port mapping.
   The Linux installer takes `--host 127.0.0.1`. The Windows installer always starts the server on all
   interfaces (`run-server.ps1` in its program folder, rewritten on every upgrade), so there, remove or
   narrow the Windows Firewall rule **AiSignX server** instead, so only the proxy can reach port 5000 (run upgrades with `-NoFirewall`, or the rule comes back).
2. Point the proxy at `http://127.0.0.1:5000` (in Docker Compose: `http://aisignx:5000`).
3. In `server/.env` (or the compose environment), set:
   - `AISIGNX_PROXY_HOPS=1`. AiSignX then reads the real client IP and `https` from the
     proxy's `X-Forwarded-For`, `X-Forwarded-Proto` and `X-Forwarded-Host` headers. Use `2`
     with two proxies in a row (for example Cloudflare in front of nginx). Leave it at `0`
     without a proxy: otherwise anyone could fake those headers, and with them the IP address
     that the login limit and the audit log record.
   - `AISIGNX_SECURE_COOKIES=1`, so login and screen cookies are only sent over HTTPS. With this
     on, signing in over plain `http://` no longer works.

Restart AiSignX and open the screens at `https://<your-host>/player/`.

**One process only.** Whatever the proxy, run a single AiSignX process behind it, with many
threads (`flask --app wsgi serve` does this). Do not load-balance several AiSignX processes or
containers: live updates, the background jobs, the login limit and maintenance mode during a
restore all live inside one process.

## What every proxy must allow

- **Live updates.** Screens keep `/api/player/v1/events` open (Server-Sent Events). The proxy
  must not buffer or compress it, and must allow at least 75 seconds without data. The server
  sends a keep-alive every 20 seconds and closes each stream after 30 minutes; the screen then
  reconnects by itself. AiSignX sends `X-Accel-Buffering: no` on this stream.
- **Large uploads.** Videos can be big. The proxy's upload limit should match
  `AISIGNX_MAX_UPLOAD_MB` (default 2048 MB), and its request time-out should allow a slow upload.
- **Uploads go through AiSignX.** Never serve the `uploads` folder directly from the proxy (an
  nginx `alias` or an IIS virtual directory, for example). Media files are only handed out
  after AiSignX checks the user or the screen's token.

## Caddy

Caddy is the simplest choice. It gets and renews certificates by itself, sends the
`X-Forwarded-*` headers, and streams live updates without buffering. It has no upload limit
unless you set one.

**Public host name (Let's Encrypt).** The DNS name must point at the machine, and ports 80
and 443 must be reachable from the internet:

```
signage.example.com {
    reverse_proxy 127.0.0.1:5000
}
```

**LAN only, no public DNS (`tls internal`).** Caddy uses its own local certificate authority:

```
signage.lan, 192.168.1.10 {
    tls internal
    reverse_proxy 127.0.0.1:5000
}
```

Caddy renews these internal certificates every few hours, so pinning the server certificate in
the player apps does not work here: the pin breaks at the first renewal. Install Caddy's root
certificate on the screens and admin PCs instead (see
[Players, the add-in and a private CA](#players-the-add-in-and-a-private-ca)). The root is in
Caddy's data folder at `pki/authorities/local/root.crt`, and it is valid for about ten years.
Run `caddy trust` to add it to the machine Caddy runs on.

Do not add `encode gzip` in front of AiSignX: it can hold back the live-update stream.

## nginx

```nginx
server {
    listen 443 ssl;
    server_name signage.example.com;
    # ssl_certificate ... ; ssl_certificate_key ... ;

    client_max_body_size 2048m;      # match AISIGNX_MAX_UPLOAD_MB
    client_body_timeout 300s;        # slow uploads
    proxy_request_buffering off;     # pass uploads through instead of spooling them to disk

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-Host $host;
        proxy_read_timeout 300s;
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
        proxy_read_timeout 3600s;
        gzip off;
    }
}
```

- `proxy_buffering off` and the `X-Accel-Buffering: no` header that AiSignX sends both keep
  nginx from holding back live updates. Keep both: some setups strip one of them.
- Do not add a `location /uploads` with `alias` or `root`. Every request must reach AiSignX.
- Redirect port 80 to 443 in a second `server` block if you like (`return 301 https://$host$request_uri;`).

## IIS with Application Request Routing (ARR)

1. Install the **URL Rewrite** and **Application Request Routing** modules.
2. In **Application Request Routing Cache > Server Proxy Settings**, tick **Enable proxy**, set
   **Response buffer threshold** to `0`, and the **Time-out** to at least 75 seconds (300 is
   better for slow uploads).
3. Add a reverse-proxy rule for the site to `http://127.0.0.1:5000`.
4. Turn off dynamic compression for the site, or at least for `text/event-stream`.
5. ARR sends `X-Forwarded-For` by default. Add a URL Rewrite server variable
   `HTTP_X_FORWARDED_PROTO` with the value `https`, and allow it under **View Server
   Variables**, so AiSignX knows the request came over HTTPS.
6. Raise the upload limit to match `AISIGNX_MAX_UPLOAD_MB`: **Request Filtering > Edit Feature
   Settings > Maximum allowed content length**, in bytes (2048 MB is `2147483648`; the IIS
   default is only about 28 MB).

## Cloudflare and Cloudflare Tunnel

Cloudflare (proxied DNS or a Tunnel) works, with these limits:

- **Uploads are capped at 100 MB** on the Free and Pro plans (more on higher plans). Larger
  videos fail with HTTP 413 from Cloudflare, not from AiSignX. Upload big files over the LAN
  address, or use a DNS-only (grey cloud) name for the admin.
- **Idle connections close after 100 seconds** (error 524). The 20-second keep-alive keeps the
  live-update stream open, so this only matters if a proxy between Cloudflare and AiSignX
  buffers it.
- **Do not cache** AiSignX pages. The default Cloudflare rules do not cache HTML or API
  responses, and AiSignX sends `Cache-Control: no-store` on player API answers; leave it that
  way and add no "cache everything" rule.
- Count the hops: a Tunnel (`cloudflared`) straight to AiSignX is `AISIGNX_PROXY_HOPS=1`;
  Cloudflare in front of nginx or Caddy is `2`.
- Turn off **Rocket Loader** and other features that rewrite pages for the AiSignX host name.

## Players, the add-in and a private CA

A certificate from a public CA (Let's Encrypt, through Caddy or certbot) needs nothing on the
devices. With a private CA (Caddy's `tls internal`, an office CA, or your own), every device that
talks to AiSignX must trust it:

- **Kiosk browsers and admin PCs:** import the CA's root certificate. On Windows, into
  **Trusted Root Certification Authorities** of the local computer (`certlm.msc`), so every
  user and service on the PC trusts it. On Linux, `/usr/local/share/ca-certificates/` then
  `update-ca-certificates`; Chromium on Linux uses its own store under **Settings > Privacy and
  security > Manage certificates**.
- **Windows player app (Electron):** uses the Windows certificate store, so import the root
  as above. Pinning a single certificate in setup also works, but only until that certificate
  changes (see [clients.md](clients.md#https-and-certificate-pinning)).
- **Android player app:** install the root under **Settings > Security > Encryption and
  credentials > Install a certificate > CA certificate**. The app trusts user CAs. Pinning works
  as for Electron.
- **PowerPoint add-in:** it uses the Windows certificate store and cannot pin. Import the root
  into **Trusted Root Certification Authorities** on every PC that publishes slides.

With a self-signed certificate and no CA, pin it in the player apps and compare the
fingerprint the app shows with the one on the server:

```bash
openssl x509 -noout -fingerprint -sha256 -in cert.pem
```

The player apps never skip certificate checks.

## Checking the setup

- Open `https://<your-host>/healthz`. It should return `{"status":"ok", ...}`.
- Sign in, then check **Admin > Audit log**: the IP address on your sign-in should be your PC's
  address, not the proxy's. If it shows the proxy's address, `AISIGNX_PROXY_HOPS` is too low.
- Open a screen with `/player/?debug=1`. Live updates should show as connected, and a change in
  the admin should reach the screen within a second or two.
