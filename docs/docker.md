# Docker

The repository root has a `Dockerfile` and a `docker-compose.yml`. The image contains the server,
ffmpeg (video length and thumbnails) and Chromium (web page previews). Everything AiSignX writes
goes to one volume mounted at `/data`.

There are no default credentials. Open the server in a browser after the first start and the
setup wizard creates the admin account.

## Build and run

With Docker Compose, from the repository root:

```bash
docker compose up -d --build
docker compose ps          # STATUS shows "healthy" after a few seconds
docker compose logs -f aisignx
```

Then open `http://<host>:5000/`. Screens use `http://<host>:5000/player/`.

Without Compose:

```bash
docker build -t aisignx .
docker run -d --name aisignx --restart unless-stopped -p 5000:5000 -v aisignx-data:/data aisignx
```

At every start the container creates its folders, applies database migrations
(`flask --app wsgi db upgrade`) and runs `flask --app wsgi serve` on port 5000. The health check
calls `/healthz` every 30 seconds.

**One container only.** Do not scale the service to more than one replica, and do not run two
containers on the same volume. Live updates to screens, the background jobs (alerts, scheduled
backups, retention), the login limit and maintenance mode all live inside one process.

## Settings

Set environment variables in `docker-compose.yml`, or in a `.env` file next to it (Compose
reads it for the `${...}` values). Never commit that file.

| Variable | In the image | Notes |
|---|---|---|
| `AISIGNX_SECRET_KEY` | empty | Empty: a key is generated once and kept in `/data/instance/secret_key`. |
| `AISIGNX_PROXY_HOPS` | `0` | `1` behind the Caddy profile or another proxy. |
| `AISIGNX_SECURE_COOKIES` | `0` | `1` once the admin is only reached over `https://`. |
| `AISIGNX_MAX_UPLOAD_MB` | `2048` | Largest upload. |
| `AISIGNX_SMTP_PASSWORD` | empty | Password for alert e-mail; the other SMTP settings are in the admin. |
| `AISIGNX_INSTANCE_DIR`, `AISIGNX_UPLOAD_DIR`, `AISIGNX_BACKUP_DIR`, `AISIGNX_LOG_DIR` | under `/data` | Leave as they are. `downloads` and `plugin-cache` follow from them and also land in `/data`. |
| `AISIGNX_BROWSER`, `AISIGNX_BROWSER_NO_SANDBOX` | `/usr/bin/chromium`, `1` | See [Web page previews](#web-page-previews). |

The other variables work too, for example `AISIGNX_MAX_STREAMS` for more than 50 screens,
`AISIGNX_LOG_LEVEL`, `AISIGNX_UPDATE_CHECK=0` to switch off the daily release check, or
`AISIGNX_UPDATE_REPO`; the full list is in [deployment.md](deployment.md#settings-and-data-folders).
Add the ones the compose file does not pass through under `environment:`. The container cannot update
itself: upgrading is `git pull` and `docker compose up -d --build`.

## Data and volumes

The `/data` volume holds:

| Folder | Contents |
|---|---|
| `/data/instance` | SQLite database `aisignx.db`, `secret_key` and `command_key` (the key that signs reboot, startup and update commands to screens; screens pin it, so it must survive a rebuild) |
| `/data/uploads` | media files, thumbnails, screen screenshots, logs collected from screens |
| `/data/downloads` | the installers on the **Downloads** page (made on first use; pushed to screens from here) |
| `/data/backups` | backup archives |
| `/data/logs` | `aisignx.log`, rotated at 5 MB x 5 files |
| `/data/plugin-cache` | fetched plugin data (can be lost; fetched again) |

All of it must persist, so keep the whole `/data` volume. `command_key` and `downloads` are **not** inside
backup archives ([backup.md](backup.md)): deleting the volume loses them even if you restore an archive.

Keep the volume on a local disk. SQLite does not work reliably on a network share (NFS, SMB).

**Bind mounts.** The server runs as user `aisignx` with uid 1000, not as root. To use a host
folder instead of a named volume, make it writable for uid 1000 first:

```bash
sudo mkdir -p /srv/aisignx
sudo chown -R 1000:1000 /srv/aisignx
```

and mount it with `- /srv/aisignx:/data`. If the folder is not writable, the container stops at
once with a message naming the folder. On Docker Desktop, a named volume is faster than a bind mount.

## Upgrade

```bash
git pull
docker compose up -d --build
```

The new container applies database migrations at start. Make a backup first (below). Going back
to an older version after a migration is not supported; restore the backup instead.

## Backups

Scheduled daily backups run inside the container, as configured in **Admin > Backups**, and are
written to `/data/backups`. That folder is on the same volume as the data, so copy archives
somewhere else as well. See [backup.md](backup.md) for what an archive contains and what it leaves out
(`instance/command_key`, the `downloads` folder). To copy the key out: `docker compose cp aisignx:/data/instance/command_key ./`.

Run the backup commands inside the running container:

```bash
docker compose exec aisignx flask --app wsgi backup create
docker compose exec aisignx flask --app wsgi backup list
```

Copy an archive out of, or into, the container:

```bash
docker compose cp aisignx:/data/backups/<name>.zip ./
docker compose cp ./<name>.zip aisignx:/data/backups/
```

**Restore** from **Admin > Backups** in the web admin, then restart the container
(`docker compose restart aisignx`). For a command-line restore, stop the server first and run the
restore in a one-off container on the same volume:

```bash
docker compose stop aisignx
docker compose run --rm --no-deps --entrypoint "" aisignx flask --app wsgi backup restore <name>.zip
docker compose start aisignx
```

A plain copy of the volume while the server runs is not a safe backup: SQLite runs in WAL mode.
Stop the container first, or use the backup commands.

## HTTPS with Caddy

The compose file has an optional Caddy service in the `https` profile. It uses
`docker/Caddyfile.example` and gets a Let's Encrypt certificate by itself.

1. Point a DNS name at the host and open ports 80 and 443 from the internet.
2. In the `.env` file next to `docker-compose.yml`, set:

   ```
   AISIGNX_DOMAIN=signage.example.com
   AISIGNX_PROXY_HOPS=1
   AISIGNX_SECURE_COOKIES=1
   ```

3. Remove the `5000:5000` port mapping from the `aisignx` service, so the app is only reachable
   through Caddy (with `AISIGNX_PROXY_HOPS=1`, a direct client could otherwise fake its IP).
4. Run `docker compose --profile https up -d`.

For a LAN without public DNS, edit the Caddyfile to use the `tls internal` block it contains,
and install Caddy's root certificate on the screens. After the first start it is in the
`caddy-data` volume:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./aisignx-root.crt
```

[https.md](https.md) explains the proxy settings and how screens, browsers and the PowerPoint
add-in trust a private certificate.

## Web page previews

The image runs Chromium for web page thumbnails. Chromium's own sandbox cannot start in a
container without extra privileges, so the image sets `AISIGNX_BROWSER_NO_SANDBOX=1` and Chromium
runs with `--no-sandbox`. This means a web page being previewed is less isolated than on a
desktop: only add web pages from sites you trust. Without Chromium, AiSignX shows a title card
instead of a preview.

## Troubleshooting

- `docker compose logs aisignx` shows the start-up and request log; `/data/logs/aisignx.log`
  keeps the application log across restarts.
- **unhealthy:** the server did not answer `/healthz`. Check the log for a migration error or a
  permission problem on `/data`.
- **Screens show the wrong IP or the login limit blocks everyone:** behind a proxy, set
  `AISIGNX_PROXY_HOPS` to the number of proxies. Without a proxy, keep it at `0`.
