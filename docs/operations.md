# Operations

How to keep an AiSignX server healthy once screens are running: alerts and e-mail, the audit
log, proof of play, health checks and logs, retention, and a short monthly checklist.

Backups have their own guide, [backup.md](backup.md). The media API and API tokens are in
[api.md](api.md); Docker and HTTPS are in [docker.md](docker.md) and [https.md](https.md).

## One server process

Run AiSignX as **one** process: `flask --app wsgi serve` (or the Docker image, with one
replica). Several things live in that process's memory only:

- live updates to screens (Server-Sent Events) and the **Reload** button;
- the sign-in rate limiter;
- the maintenance flag set while a backup is restored, and the backup progress shown in the admin;
- the background jobs thread and the schedule ticker.

A second process would not see the first one's limiter or maintenance flag. The background jobs
are protected against running twice, but everything else is not. Scale up with threads
(`AISIGNX_MAX_STREAMS`), not processes.

## Background jobs

A jobs thread starts with the server (on the first request under `flask run`) and wakes every
30 seconds. Each job records its last run, last success and last error in the database, so a
restart does not make a daily job run again.

| Job | Runs every | Does |
|---|---|---|
| `alerts.sweep` | minute | Opens and resolves alerts |
| `alerts.mail` | minute | Sends alert e-mails, when turned on |
| `alerts.purge` | day | Deletes resolved alerts past their retention |
| `backup.scheduled` | 15 minutes | Makes the daily backup once its hour has passed |
| `audit.purge` | 6 hours | Deletes audit rows past their retention |
| `pop.rollup` | hour | Builds the daily proof-of-play totals and purges old plays |
| `media.orphans` | day | Deletes upload files no media item uses (older than 1 hour) and temp files older than a day |

`AISIGNX_JOBS=0` turns the thread off; nothing above then happens. **Admin > System** lists
every job with its last run and error. A job that fails three times in a row opens a
`job_failed` alert.

## Alerts

**Alerts** in the top bar shows open alerts, with a red badge counting those not yet
acknowledged. Everyone can see the page; editors and admins can acknowledge an alert and mute
a display. Alerts are worked out from facts every minute, so they open and close on their own:

| Alert | Opens when | Resolves when |
|---|---|---|
| Display offline | A paired screen has not been seen for `offline minutes` (default 10). Critical after 24 hours. | The screen is back, unpaired, deleted or muted |
| Low disk | Free space for uploads, backups or the database is below the GB **or** % threshold (default 2 GB / 5 %). Critical below half of either. | Free space is above 1.2 times both thresholds |
| Sign-in burst | At least `login burst` (default 20) failed sign-ins in 10 minutes. Lists the top addresses and user names. | 30 minutes with no failed sign-in |
| Backup failed | The last backup attempt failed | A later backup succeeds |
| Backup stale | Daily backups are on and the newest good one is over 48 hours old | A fresh backup exists |
| Job failed | A background job failed 3 or more times in a row | The job succeeds |

After a server restart, offline alerts wait `offline minutes` plus 2 minutes, so screens that
are still reconnecting do not raise alerts. **Mute display** next to an offline alert stops
offline alerts for that screen (one that is switched off at night, say); the alerts page lists
muted displays with an **Unmute** button.

Acknowledging only clears the badge; the alert stays open until its cause is gone.

### E-mail

Under **Alerts > Settings** (admin) fill in the SMTP server, port, security (STARTTLS, SSL/TLS
or none), user name, from address and the recipients (separated by commas), then turn on
**Send alert e-mails**. **Send test e-mail** checks the settings straight away.

The SMTP password is never stored in the database. Put it in `server/.env`:

```
AISIGNX_SMTP_PASSWORD=...
```

and restart the server. The settings page shows whether it is set, never the value.

Each recipient gets their own message: one when an alert opens and one when it resolves. A
failed send is retried with growing pauses (up to an hour). Alerts older than 24 hours are not
mailed any more, so a long mail outage does not end in a flood.

## Audit log

**Admin > Audit log** records who did what: sign-ins (also failed and rate-limited ones), user,
display, group, playlist, schedule, media, emergency and settings changes, API token use,
backups and restores. Each row has the time, user, how (`web`, `api`, `cli` or `system`), the
IP address, the item and a detail. Passwords, tokens, PINs and keys are never written to it.

- Filter by user, action, item and dates (in the organization's time zone); **Export CSV**
  downloads the filtered rows.
- Edit pages for displays, playlists, schedules, groups, users and media have a **History**
  link (admins only) that opens the log filtered to that item.
- Rows are kept for 365 days by default. Change it on the audit page; 0 keeps them forever.

## Sign-in protection

Failed sign-ins are limited per IP address (20) and per user name (5) within 15 minutes. Past
the limit the login page answers "Too many failed sign-in attempts" with HTTP 429 until the
window has passed; the answer is the same whether the user exists or not. A failed API token
check counts against the same per-IP limit. Change the limits with
`AISIGNX_LOGIN_MAX_PER_IP`, `AISIGNX_LOGIN_MAX_PER_USER` and `AISIGNX_LOGIN_WINDOW_MIN`.

Behind a reverse proxy, set `AISIGNX_PROXY_HOPS` (see [https.md](https.md)); otherwise every
visitor seems to come from the proxy's address and shares one limit.

## Proof of play

Proof of play is **off** by default. An admin turns it on at the bottom of **Reports > Plays**.
Screens then report each item they finished showing, with its start time and length. Items
that failed to load and time hidden behind a full-screen emergency message are not counted.
Screens keep up to 1000 plays while offline and send them later.

**Reports > Plays** (every role) shows plays, total time and number of screens per media item,
display or day, for a date range and an optional display, group or media item. Totals are
built every hour, so today's figures run up to the last hourly rollup. Two CSV downloads exist:

- **Daily CSV:** day, display, media, plays and seconds. Kept for 400 days by default.
- **Raw CSV:** every single play. Kept for 14 days by default.

About 30 screens changing item every 10 seconds produce some 260,000 plays a day. If the
database grows too large, lower the raw retention; the daily totals are unaffected.

## Health checks and logs

- **`/healthz`** needs no sign-in and answers `{"status": "ok", "app": "aisignx", "api": 1}`
  with HTTP 200, or HTTP 503 with `"status": "error"` when the database cannot be reached. Use
  it for uptime monitors and the Docker health check.
- **Admin > System** runs a self-test with timings: database, schema version, writable folders,
  free disk space, the schedule ticker, the jobs thread and every job, live-update connections,
  the last backup and the mail settings. **Run self-test** also tries to connect to the mail
  server. Below the checks is runtime information (versions, paths, CPU and memory).
- **`flask --app wsgi check --all`** runs the same self-test from a terminal (add `--json` for
  scripts, `--smtp` to include the mail server). It exits with 1 when a check fails. Without
  `--all` it only checks for ffmpeg and the browser.
- **Admin > Logs** shows the end of `aisignx.log` (200, 500 or 1000 lines, optionally only
  warnings and errors) with an optional refresh every 10 seconds, and downloads the file. The
  log lives in `AISIGNX_LOG_DIR` (default: `server/logs`), rotates at 5 MB and keeps 5 old files.
  `AISIGNX_LOG_LEVEL=DEBUG` gives more detail.

## Retention at a glance

| Data | Default | Where to change |
|---|---|---|
| Audit log | 365 days | Admin > Audit log |
| Resolved alerts | 90 days | Alerts > Settings |
| Raw plays | 14 days | Reports > Plays (admin) |
| Daily play totals | 400 days | Reports > Plays (admin) |
| Scheduled backups | newest 7 | Admin > Backups |
| Log files | 5 x 5 MB | fixed |

Manual, uploaded and pre-restore backups are never deleted automatically.

## Monthly checklist

1. Open **Alerts**: nothing should stay open for long. Mute screens that are switched off on
   purpose instead of ignoring their alerts.
2. Open **Admin > System** and press **Run self-test**. Everything should be green; fix what
   is not (each check has a hint).
3. Open **Admin > Backups**: the newest scheduled backup is from the last day. Check one with
   `flask --app wsgi backup verify <name>` in `server/`, and check that an off-site copy exists
   (see [backup.md](backup.md)).
4. Look at **Admin > Logs** with the level set to warnings for repeated errors.
5. Skim the **Audit log** for failed sign-ins and for users or tokens you do not expect.
6. Under **Admin > All API tokens**, revoke tokens nobody uses any more.
7. Check free disk space on the server, and install updates for the operating system, ffmpeg
   and the browser. After updating AiSignX itself, run `flask --app wsgi db upgrade`.
