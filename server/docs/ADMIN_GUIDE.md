# AISignX — Administrator Guide

Covers all administrator tasks: user management, display management, API tokens, system settings, and maintenance.

---

## Table of Contents

1. [Admin vs. Regular Users](#1-admin-vs-regular-users)
2. [User Management](#2-user-management)
3. [Display Management](#3-display-management)
4. [Display Groups](#4-display-groups)
5. [Approving Display Registrations](#5-approving-display-registrations)
6. [API Tokens](#6-api-tokens)
7. [Downloads Page](#7-downloads-page)
7b. [Pushing Commands to Displays](#7b-pushing-commands-to-displays)
7c. [Bulk Editing and Filters](#7c-bulk-editing-and-filters)
7d. [Display Diagnostics](#7d-display-diagnostics)
8. [Plugins Management](#8-plugins-management)
9. [System Settings](#9-system-settings)
9b. [Proof of Play](#9b-proof-of-play)
10. [Database Maintenance](#10-database-maintenance)
11. [Log Files](#11-log-files)

---

## 1. Admin vs. Regular Users

| Capability | Admin | Regular User |
|---|---|---|
| Manage media, playlists, schedules | Yes | Yes |
| Send emergency broadcasts | Yes | Yes |
| Approve/decline display registrations | Yes | No |
| Create and manage users | Yes | No |
| Create and manage API tokens | Yes | No |
| Access Downloads page | Yes | No |
| Access Plugins page | Yes | No |
| Access Browser Access Link | Yes | No |
| Delete other users content | Yes | No |

---

## 2. User Management

Go to **Users** in the sidebar (admin only).

User access is tenant-aware:
- Superusers can see users across all tenants and filter the list by tenant.
- Regular tenant admins only manage users/roles within their active tenant.
- Bulk role editing supports assigning or removing roles from selected users.
- Custom roles are managed from **Custom Roles** and are scoped to the tenant
  unless created by a superuser for broader administration.

### Creating a User

1. Click **New User**
2. Enter username, email, and a temporary password
3. Check **Administrator** if the user needs admin rights
4. Click **Create User**
5. Share the temporary password with the user and ask them to change it on first login

### Editing a User

Click the pencil icon next to any user to edit their username, email, or admin status.

### Resetting a Password

Click the key icon next to a user, enter a new password, and click **Save**.

### Disabling a User

Toggle the **Active** switch to disable a user account without deleting it. Disabled users cannot log in.

### Deleting a User

Click the trash icon. This does not delete any content they created.

---

## 3. Display Management

Go to **Displays** in the sidebar.

### Adding a Display Manually

1. Click **Add New Display**
2. Enter a name, location (optional), and other settings
3. Click **Save**
4. The display detail page shows the player URL and API token
5. Open the player URL on the display device: `http://your-server/display/<token>`

### Display Detail Page

Each display detail page shows:

| Section | Description |
|---|---|
| Status | Online/offline, last ping time, IP address, current content |
| Player URL | Open this on the display device |
| API Token | Used by native clients — treat as a secret |
| Default Playlist | Plays when no schedule is active |
| Settings | Aspect mode, resolution, orientation, input lock, media buttons |
| Regenerate Token | Issue a new token (old one stops working immediately) |
| Delete Display | Permanently removes the display and its schedules |

### Online/Offline Status

A display is considered **Online** if it has sent a ping within the last 2 minutes. The status updates automatically every 30 seconds on the Displays list page.

### Regenerating the API Token

If a display token is compromised:
1. Open the display detail page
2. Click **Regenerate Token**
3. Update the native client or player URL on the device with the new token

---

## 4. Display Groups

Groups let you target schedules and emergency broadcasts at multiple displays at once.

### Creating a Group

1. Go to **Displays ? Groups** (or **Displays** sidebar sub-item)
2. Click **New Group**
3. Enter a name and optional description
4. Add displays using the multi-select
5. Click **Save**

### Using Groups

- When creating a **Schedule**, set the target to a group instead of a single display
- When sending an **Emergency Broadcast**, set the target to a group to alert all displays in it simultaneously
- A display can belong to only one group at a time

---

## 5. Approving Display Registrations

When a native client (Electron, Android) or browser registers, it appears in **Displays ? Pending** with a status of "Pending".

### Approving

1. Go to **Displays** — pending registrations appear at the top
2. Review the device name, hostname, OS, and IP address
3. Click **Approve** — the display is added and the client auto-loads the player
4. Optionally rename the display after approval

### Declining

Click **Decline** — the client shows a "Registration Declined" message. The pending record is kept for reference and can be deleted later.

### Browser Self-Registration

Browsers use the `/request-access` page to self-register. The flow is identical to native clients — the browser polls every 5 seconds and auto-loads the player on approval.

The **Browser Access Link** in the sidebar opens `/request-access` in a new tab for easy sharing.

---

## 6. API Tokens

API tokens allow external systems to interact with AISignX via the REST API without browser session cookies.

### Creating a Token

1. Go to **Settings ? API Tokens** (sidebar: API Tools)
2. Click **New Token**
3. Enter a name (e.g. "Fire Alarm Integration")
4. Select scopes — leave blank for full access
5. Click **Create**
6. **Copy the token immediately** — it is only shown once

### Using a Token

Include the token in the `Authorization` header:
```
Authorization: Bearer <your-token>
```

### Revoking a Token

Click **Revoke** next to any token. It stops working immediately.

### Token Scopes

| Scope | Access |
|---|---|
| (blank) | Full access to all API endpoints |
| `media:read` | Read media library only |
| `media:write` | Upload and manage media |
| `emergency:write` | Trigger and clear emergency broadcasts |
| `schedules:write` | Create and manage schedules |

---

## 7. Downloads Page

The Downloads page (`/downloads`) serves the native client installers to end users.

### Managing Client Files

Built client packages go in `static/clients/`:

| File | Platform |
|---|---|
| `AISignX-Player-Setup.exe` | Windows installer |
| `AISignX-Player.AppImage` | Linux portable |
| `AISignX-Player.deb` | Linux Debian/Ubuntu package |
| `AISignX-Player.apk` | Android APK |

### Publishing a New Client Version

1. Build the new packages (see [CLIENTS.md](CLIENTS.md))
2. Copy them to `static/clients/`
3. Go to **Downloads** ? expand the **Manifest Editor** card
4. Update the version numbers and click **Save**
5. Running clients detect the update on their next check — OR you can immediately push it to specific displays via **Update Client** (see next section)

> **Important:** Always edit `client_versions.json` via the Manifest Editor. Direct edits with PowerShell `Set-Content` or Notepad sometimes add a UTF-8 byte-order-mark that older Python parsers reject. (The current server is BOM-tolerant, but the editor is the safest path.)

---

## 7b. Pushing Commands to Displays

From the **Display detail page** you can push three one-off commands to any connected display:

| Button | Effect | Works on |
|---|---|---|
| **Reload** | Reload the player page | All clients |
| **Reboot App** | Restart the player application | Electron and Android native clients; browser falls back to reload |
| **Update Client** | Download + install + relaunch of the latest client version | Electron and Android native clients. Android silent install requires Device Owner |

### Reload

Use when a display is stuck, a slide is frozen, or you want to immediately pick up a configuration change without waiting for the next playlist transition. Browser clients reload the page; Electron clients reload the kiosk window.

### Reboot App

Restarts the native player process/activity. Useful for memory cleanup on
long-running displays or to recover from a soft hang. Electron exits and
relaunches via `app.relaunch()`; Android restarts `PlayerActivity`.

Browser clients fall back to a page reload (they have no application to reboot).

### Update Client

The most powerful push command updates native client software.

Electron Windows total flow:

1. Player downloads the latest installer from `/static/clients/AISignX-Player-Setup.exe`
2. Player schedules a Windows Task Scheduler one-shot task ~75 seconds in the future
3. Player exits cleanly
4. Task fires ? silent install runs (`/S` flag, no UAC because we're per-user)
5. New client launches in fullscreen kiosk mode

Total downtime: ~80 seconds of black screen on the display. No user interaction required — perfect for off-hours updates pushed in bulk.

Android total flow:

1. Player reads the Android entry from `/api/version`
2. Player downloads `static/clients/AISignX-Player.apk`
3. Player commits an Android `PackageInstaller` session
4. If the app is Device Owner, Android can approve the update without a tap
5. If not Device Owner, Android may show its system install approval prompt

**Pre-requisites:**
- The new build must be in `static/clients/AISignX-Player-Setup.exe`
- Android builds must be in `static/clients/AISignX-Player.apk`
- `client_versions.json` must show a higher version than the one currently installed
- The display must be online (have an active SSE connection)
- Android silent install requires Device Owner / managed kiosk provisioning

If the display is offline, you'll see a popup saying the command was discarded. Wait for the display to reconnect, then try again.

### Diagnosing a failed Update Client

If a push update doesn't appear to take effect, check the display's logs:

| File | Path on display |
|---|---|
| Main app log | `%APPDATA%\aisignx-player\aisignx-player.log` |
| Update wrapper log (per-step trace) | `%APPDATA%\aisignx-player\aisignx-update-wrapper.log` |
| PowerShell errors during update | `%APPDATA%\aisignx-player\aisignx-update-wrapper.stderr.log` |

Or remotely: ask whoever is at the display to press **Ctrl+Alt+L** — that opens the main log in Notepad. (Hotkeys are listed in [CLIENTS.md](CLIENTS.md#diagnostic-hotkeys-electron-only).)

---

## 7c. Bulk Editing and Filters

List pages now follow the same admin pattern where editing makes sense:

- Header with primary actions
- Filter toolbar with search and relevant dropdown filters
- Row checkboxes and select-all
- Selected-count bulk action bar
- Confirmation before destructive actions
- Server-side permission and tenant checks on every bulk endpoint

Current bulk-capable areas include:

| Area | Bulk actions |
|---|---|
| Displays | Group, location, aspect mode, auto-update, input/offline/media controls, volume, unlock PIN, diagnostics, delete, and pushed commands |
| Pending registrations | Approve or decline selected clients |
| Groups | Edit selected group metadata |
| Media | Set duration, use detected video length, edit tags, move folders, rename, delete |
| Playlists | Edit playlist metadata, delete |
| Playlist items | Set transition, set duration, use detected video length, toggle mute, delete |
| Users | Edit status/roles and bulk delete where permitted |
| Tenants | Filter, bulk edit, and bulk delete with safety checks |
| Schedules | Filter and bulk edit active/target/timing fields |

Superusers see all tenants by default on cross-tenant admin pages and can filter
by tenant. Regular admins and users remain tenant-scoped server-side; UI filters
do not grant access.

---

## 7d. Display Diagnostics

Diagnostics are opt-in per display. Enable **Diagnostics Enabled** on a display
when you need to capture client-side player behavior on the server.

Where to view:

- Display detail page: recent logs for that display
- **Administration -> Display Diagnostics**: central diagnostics viewer

Central filters include tenant, display, group, level, search text, and limit.
Superusers can inspect all tenants; non-superusers only see their active tenant.

Captured events include console errors/warnings, sync calibration and drift
events, network/offline changes, and player runtime diagnostics. Leave
diagnostics disabled when not troubleshooting.

---

## 8. Plugins Management

Go to **Plugins** in the sidebar (admin only).

The Plugins page shows all installed plugins with their name, version, and description.

### Reloading the Plugin Registry

If you install a new plugin while the server is running:
1. Drop the plugin folder into `plugins/`
2. Click **Reload Registry** on the Plugins page

The new plugin is available immediately without restarting the server.

### Installing a Plugin

1. Copy the plugin folder (containing `plugin.json` and `main.js`) to `plugins/your-plugin-name/`
2. Click **Reload Registry**
3. The plugin now appears when adding a plugin-type media item

For writing custom plugins see [PLUGINS.md](PLUGINS.md).

---

## 9. System Settings

`config.py` controls all server-level settings. Edit it directly and restart the server for changes to take effect.

### Changing the Database

For production with many displays, switch from SQLite to PostgreSQL:

```python
SQLALCHEMY_DATABASE_URI = 'postgresql://user:password@localhost/aisignx'
```

Install the driver: `pip install psycopg2-binary`

Run migration after: `python migration.py`

### Changing the Upload Folder

```python
UPLOAD_FOLDER = '/mnt/nas/aisignx-uploads'
```

Move existing files to the new path: `mv uploads/* /mnt/nas/aisignx-uploads/`

### Enabling HTTPS Redirect

In `config.py`:
```python
PREFERRED_URL_SCHEME = 'https'
```

Set up SSL termination at the reverse proxy level (nginx/Caddy) — see [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md).

---

## 9b. Proof of Play

Proof of Play records which content played on which display and when. It is
optional and off until enabled in **System Settings** (`proof_of_play.enabled`).

### Who can view it

| Role | Access |
|---|---|
| **Superuser** | All tenants; use the **Tenant** filter (**All tenants** or a specific tenant) |
| **Tenant admin** (`domain.admin`) | Only tenants they administer; scoped to the **Active tenant** in the sidebar by default |

Requires the `audit.read` permission (included with `domain.admin` and the
system **viewer** role). The page is under **Administration ? Proof of Play**
(next to Audit Log).

### Using the page

1. Open **Proof of Play**
2. Filter by date range, displays, media type, or plugin
3. **Export CSV** downloads the current filter set
4. Superusers can toggle **Recording enabled** and run **Purge expired** (retention sweep)

Tenant admins do not see enable/purge controls — those are superuser-only.

Playback rows are stored per tenant (`proof_of_play.domain_id`). The player
ingest endpoint (`POST /api/display/<token>/proof-of-play`) always stamps the
display's tenant. See [OPERATIONS.md](OPERATIONS.md) for retention settings.

---

## 10. Database Maintenance

### Backing Up (SQLite)

```bash
cp signage.db signage.db.backup-$(date +%Y%m%d)
```

Or use SQLite's built-in backup:
```bash
sqlite3 signage.db ".backup signage-backup.db"
```

### Backing Up (PostgreSQL)

```bash
pg_dump aisignx > aisignx-backup-$(date +%Y%m%d).sql
```

### Restoring a Backup

**SQLite:**
```bash
cp signage.db.backup-20250101 signage.db
```

**PostgreSQL:**
```bash
psql aisignx < aisignx-backup-20250101.sql
```

### Running Migrations

After a code update that changes the database schema:
```bash
python migration.py
```

This is safe to run at any time — it only applies changes that have not been applied yet.

---

## 11. Log Files

AISignX logs to the console by default. In production, redirect output to a log file:

**systemd (Linux):**
Logs are captured automatically by journald:
```bash
journalctl -u aisignx -f          # live tail
journalctl -u aisignx -n 200      # last 200 lines
journalctl -u aisignx --since "1 hour ago"
```

**Windows / manual:**
```bash
python app.py >> logs\aisignx.log 2>&1
```

### Log Levels

Set the log level in `config.py`:
```python
LOG_LEVEL = 'INFO'   # DEBUG | INFO | WARNING | ERROR
```

Key log entries to watch for:
- `SSE emergency pushed` — emergency broadcast sent to a display
- `SSE emergency cleared` — emergency cleared
- `Emergency broadcast ACTIVATED` / `CLEARED` — with username
- `SSE connection closed` — display disconnected
- `SSE reload pushed` — playlist update sent to display