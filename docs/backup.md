# Backup and restore

AiSignX writes backups as single ZIP archives. You can make them from the web admin
(**Admin > Backups**, admins only) or from the command line. All commands below run in the
`server` folder:

```powershell
.venv\Scripts\flask --app wsgi backup --help     # Linux/macOS: .venv/bin/flask --app wsgi backup --help
```

## What a backup contains

| Archive member | What it is |
|---|---|
| `db/aisignx.db` | A consistent copy of the SQLite database, made with SQLite's backup API while the server runs, and checked with `PRAGMA integrity_check`. |
| `instance/secret_key` | The session signing key, if it is stored in the instance folder (that is, when `AISIGNX_SECRET_KEY` is not set). |
| `uploads/media/*`, `uploads/thumbs/*` | Uploaded media and their previews, if "include uploads" is on. Screenshots and temporary files are left out. |
| `manifest.json` | Format, AiSignX version, database revision, creation time, and the size and SHA-256 of every file. |

**Backups contain secrets:** password hashes, API token hashes, the secret key and the SMTP
user name. Treat every archive like a password file. Store copies only where untrusted people
cannot read them.

Not included: `server/.env` (environment settings such as `AISIGNX_SECRET_KEY` and
`AISIGNX_SMTP_PASSWORD`), log files and screenshots. Keep a private copy of your `.env`
yourself.

Archives are named `aisignx-<UTC date>-<time>-<source>-<random>.zip`. The source is one of
`manual` (web), `cli`, `scheduled` (daily), `pre-restore` (safety copy taken before a restore)
or `uploaded`.

## Where backups go

The default folder is `server/backups`. To use another one, set `AISIGNX_BACKUP_DIR` in
`server/.env` and restart. The web admin shows the folder, but you can't change it there. A
second local disk is a good place for it. A network share also works, because each archive is
written to a `.part` file first and renamed only once it is complete.

## Daily backups and retention

On **Admin > Backups**, under **Daily backups**:

- **Back up automatically every day** (on by default).
- **At or after this hour** (default 3, in the organization's time zone). The server checks
  every 15 minutes, so if it was off at that hour, the backup runs as soon as it is back.
- **Keep this many daily backups** (default 7). Older daily backups are deleted. Manual, CLI,
  uploaded and pre-restore backups are **never** deleted automatically; delete them yourself.
- **Include uploaded media in daily backups** (default on). Turn it off if your media is large
  and stored elsewhere.

If a daily backup fails, the error is shown on the Backups page and the server tries again
an hour later. When daily backups are on and the newest archive is more than 48 hours old,
the page shows a warning (and the Alerts page raises a "backup stale" alert).

Leftover `.part` files and `.staging-*` folders from a crash are removed after a day.

From the command line:

```powershell
flask --app wsgi backup create              # database, secret key and uploads
flask --app wsgi backup create --no-uploads # database and secret key only
flask --app wsgi backup list
flask --app wsgi backup prune               # apply "keep" to daily backups now
```

## Copy backups off-site

A backup on the same disk does not survive a disk failure. Copy the backup folder to another
machine or to cloud storage every day, for example with a scheduled task:

```powershell
robocopy C:\AiSignX\server\backups \\nas\backups\aisignx aisignx-*.zip /XO
```

On Linux, use `rsync -a server/backups/ backuphost:aisignx/` from cron. Copy only the finished
`aisignx-*.zip` files, never `.part` files. You can also download any archive from the
Backups page.

## Check a backup

```powershell
flask --app wsgi backup verify aisignx-20261112-030001-scheduled-1a2b.zip
```

`verify` checks every checksum, the file list and the database revision. `FILE` can be a
name from the backup folder or a path to an archive anywhere.

## Restore from the web admin

1. Open **Admin > Backups**. To restore an archive from another machine, upload it first
   with **Upload a backup**. Uploads are checked before they are listed. If an archive is
   larger than the upload limit (`AISIGNX_MAX_UPLOAD_MB`), copy it into the backup folder
   instead.
2. Click **Restore...** next to the archive, check the summary, type the file name and confirm.
3. AiSignX then:
   - shows a maintenance page to everyone and sends players a "retry later" answer (screens
     keep playing what they have);
   - writes a `pre-restore` safety backup, and **cancels the restore if that fails** (nothing
     is changed);
   - writes the database in place, syncs the uploads (files missing from the archive are
     deleted), restores the secret key, and upgrades the database if the archive came from an
     older version;
   - records the restore in the audit log and tells all screens to reload their content.
4. Everyone, including you, has to sign in again.
5. **Restart the server.** A restored secret key only takes effect after a restart, and a
   restart clears every old session.

If any step after the safety backup fails, AiSignX puts the safety backup back automatically
and shows both errors. On Windows, a media file that is open in another program cannot be
replaced. Such files are listed after the restore, not treated as errors. Close the program
and restore again, or copy the file by hand.

An archive made by a **newer** AiSignX version is refused ("Upgrade first"). Databases are
never migrated down.

Maintenance mode, the restore status and the safety backup lock only apply to the one server
process. Run AiSignX as a single process (see [deployment](deployment.md)).

## Restore from the command line

Stop the server first, then run:

```powershell
flask --app wsgi backup restore aisignx-20261112-030001-scheduled-1a2b.zip
```

The command asks for confirmation; `--yes` skips the question. It takes the same steps as the
web restore, including the safety backup. Start the server again afterwards.

## Moving to a new machine

1. On the old machine, run `flask --app wsgi backup create` (or download a backup from the
   web admin), and copy `server/.env` too.
2. Install AiSignX on the new machine (same or newer version). Copy `.env` into `server/`, and
   change paths in it if needed.
3. Run `flask --app wsgi db upgrade` to create an empty database. Don't go through the setup
   wizard; the restore replaces the admin account anyway.
4. Copy the archive into `server/backups` and run `flask --app wsgi backup restore <name>`.
5. Start the server and sign in with your old account. Paired screens keep working if they
   reach the new machine at the same address (change DNS or the screen's server URL).

## When the server won't start

The CLI restore does not need the web server. If a bad update or a damaged database stops
AiSignX from starting:

1. Run `flask --app wsgi backup list` to find the newest good archive, then
   `flask --app wsgi backup verify <name>` to check it.
2. Run `flask --app wsgi backup restore <name>`.
3. If the restore is cancelled because the safety backup fails (the current database is
   damaged), move `server/instance/aisignx.db` (plus its `-wal` and `-shm` files) out of the
   way, run `flask --app wsgi db upgrade`, then restore again.

As a last resort, an archive is an ordinary ZIP file. Extract `db/aisignx.db` to
`server/instance/aisignx.db`, `instance/secret_key` to `server/instance/secret_key` and
`uploads/` to `server/uploads/`, with the server stopped, then run `flask --app wsgi db upgrade`.

## PostgreSQL

With a database URL other than SQLite, archives contain only the secret key and the uploads.
The manifest records `"db_included": false`, and the web page says "no database". Back up the
database with PostgreSQL's own tools, alongside the uploads-only archive:

```bash
pg_dump -Fc -f aisignx-$(date +%Y%m%d).dump "$DATABASE_URL"
```

To restore, stop AiSignX first, then run:

```bash
pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" aisignx-20261112.dump
flask --app wsgi backup restore <uploads-only archive>.zip --yes
```

Use a dump and an archive taken at about the same time. AiSignX refuses to restore an
archive that contains a SQLite database onto a PostgreSQL server.
