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

Two more files live next to the database and matter when you move or restore a server: the
instance folder's `secret_key` and `command_key`; both are in every backup (see [Files that are not in a backup](#files-that-are-not-in-a-backup) for what is not).

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
| `media.webpages` | minute | Checks whether web pages allow being embedded (once a day each) and retakes the screenshots of pages shown as screenshots when their refresh interval has passed (2 per run) |
| `media.probe` | 5 min | Reads the codec, size, bitrate and frame rate of videos uploaded before those were recorded (10 per run), for the "May stutter" warnings |
| `media.orphans` | day | Deletes upload files no media item uses (older than 1 hour; not in the first day after a restore) and temp files older than a day |
| `agent.commands` | minute | Expires device commands a screen never carried out, deletes finished ones after 30 days, and deletes uploaded device logs older than 7 days |
| `emergency.schedule` | 30 seconds | Sends scheduled emergency messages when their time comes and clears the ones whose duration is over ([emergency-api.md](emergency-api.md)) |
| `updates.check` | day | Asks GitHub for the latest release (see [updates.md](updates.md)); does nothing when the check is off |
| `plugin_cache_prune` | day | Removes plugin data nobody read for 7 days and keeps the plugin cache under its size cap |

`AISIGNX_JOBS=0` turns the thread off; nothing above then happens. **Admin > System** lists
every job with its last run and error. A job that fails three times in a row opens a
`job_failed` alert.

## Alerts

**Alerts** in the menu shows open alerts, with a red badge counting those not yet
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
notice counts as sent once any recipient accepted it; the addresses that failed are shown on
the alert and are not tried again, so the others never get the same mail twice. A notice no
recipient accepted is retried with growing pauses (up to an hour), unless the server refused
every address for good (5xx). All notices of one run share one connection: when the server
cannot be reached, the run stops after one try and every notice waits for its next turn.
Alerts older than 24 hours are not mailed any more, so a long mail outage does not end in a
flood.

## Audit log

**Admin > Audit log** records who did what: sign-ins (also failed and rate-limited ones), user,
display, group, playlist, schedule, media, emergency (also scheduled and API sends) and settings changes,
device commands and the privileged agent (enrolment, removal, results), Android provisioning, Downloads
uploads and removals, update-check changes, API token use, backups and restores. Each row has the time, user, how (`web`, `api`, `cli` or `system`), the
IP address, the item and a detail. Passwords, tokens, PINs and keys are never written to it.

- Filter by user, action, item and dates (in the organization's time zone); **Export CSV**
  downloads the filtered rows.
- Edit pages for displays, playlists, schedules, groups, users and media have a **History**
  link (admins only) that opens the log filtered to that item.
- Rows are kept for 365 days by default. Change it on the audit page; 0 keeps them forever.

## Sign-in protection

Failed sign-ins are limited per IP address (20; an IPv6 /64 network counts as one address)
and per user name (5) within 15 minutes. Past
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

## Files that are not in a backup

A backup holds the database, the `secret_key`, the `command_key` (the key that signs reboot, startup and update
commands to screens, see [signed-commands.md](signed-commands.md)) and the uploaded media (see [backup.md](backup.md)).
It does **not** hold:

- The **downloads** folder (the installers on the Downloads page, `downloads` next to `uploads`). The database
  remembers the entries, so after a restore onto a bare machine they are listed but cannot be downloaded or
  pushed to screens until you upload the files again.
- Device logs collected from screens (`uploads/agent-logs`), the plugin cache, `.env` and the log files.

## Retention at a glance

| Data | Default | Where to change |
|---|---|---|
| Audit log | 365 days | Admin > Audit log |
| Resolved alerts | 90 days | Alerts > Settings |
| Raw plays | 14 days | Reports > Plays (admin) |
| Daily play totals | 400 days | Reports > Plays (admin) |
| Scheduled backups | newest 7 | Admin > Backups |
| Log files | 5 x 5 MB | fixed |
| Finished device commands | 30 days | fixed |
| Device logs uploaded from screens | newest 5 per screen, and 7 days | fixed |

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
7. Look at **Admin > Updates** to see whether a newer AiSignX release exists, and at the **Displays** list for
   screens whose app is older than the newest installer on **Downloads**.
8. Check free disk space on the server, and install updates for the operating system, ffmpeg
   and the browser. After updating AiSignX itself, run `flask --app wsgi db upgrade`.

## Managing many screens

- **Displays list:** tick screens (first column), then **Edit selected together** to change the same settings on all of them, or **App updates**.
  In the bulk edit every field starts at *No change*; the kiosk lock, names, notes and PINs stay per screen.
- **App updates (admin):** lists each screen's app version against the newest installer on the Downloads page, lets you tick which screens to update, and shows each
  one's progress (waiting, installing, installed, failed with the reason). The table and the display page refresh by themselves; leave the tab open while an update runs.
  A screen is updated only if its *Allow installing updates* switch is on and its app can do it; otherwise it is refused with the reason.
- The display's **Edit** page words its settings for the screen's system (Windows, Android, Linux, browser) once the player has reported what it is.

## Brightness

Displays > Edit > Screen: **Brightness** (10 to 100 %) and an optional **night window** (a lower level between two local times, in the organization's time zone).
- **Android app 3.0.8 and newer:** the app sets the real screen backlight (the window's brightness), so it saves power too. As Device Owner (3.0.10 and newer) it also sets the device's own brightness setting, and 100 % is full brightness; the device's slider shows the same percent. Without Device Owner, at 100 % the device's own brightness setting applies. An emergency message is shown at full brightness. On Android TV (3.0.11 and newer) the app does not report the `brightness` capability, so the page darkens the picture: a TV box sends HDMI and has no backlight to set.
- **Windows app 3.6.0 and newer:** the app sets the screen's own brightness: built-in panels (laptops, all-in-ones, tablets) through Windows, external monitors through DDC/CI if the monitor supports it (often an on-screen menu option; some monitors, TVs and adapters do not pass it through). The app leaves the brightness alone until a display asks for less than 100 %; after that, 100 % sets the screen back to full. An emergency message is shown at full brightness. When no screen can be set, the app darkens the picture instead, as before. Tried on a built-in panel; DDC/CI not yet tried on a real monitor.
- **Older Windows apps and browsers:** the player darkens its picture with a black layer; the monitor's own backlight is not touched (set it on the monitor).

## Adding media to a playlist

The **Add media** box on a playlist searches and filters the whole library on the server, so it stays quick with hundreds of pictures: filter by kind, plugin or source, search, order,
hide what is already in the playlist, and **Select all matching**. What you tick is kept while you change filters. Pictures copied from an image source are hidden until you pick that source (or Everything).
