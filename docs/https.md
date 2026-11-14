# HTTPS and reverse proxies

AiSignX itself speaks plain HTTP. For HTTPS, put a reverse proxy in front of it. Use HTTPS when
the network is not fully trusted: guest Wi-Fi, shared offices, or anything reachable from the
internet.

This page covers Caddy, nginx, IIS and Cloudflare. For Docker, the compose file has a ready-made
Caddy profile (see [docker.md](docker.md#https-with-caddy)).

## The three steps for every proxy

1. Make AiSignX listen on the local machine only, so nobody can bypass the proxy:
   `flask --app wsgi serve --host 127.0.0.1`. In Docker, remove the `5000:5000` port mapping.
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
