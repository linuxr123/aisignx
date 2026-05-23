# Operations Reference

Day-2 operator's guide for AISignX Phase 1+: settings, audit log,
background jobs, storage, and the leak scanner.

---

## System settings

Settings live in the `system_setting` table. They have two scopes:

| Scope    | `domain_id` | Affects                          |
|----------|-------------|----------------------------------|
| Global   | `NULL`      | the whole server                 |
| Per-tenant | `<id>`    | overrides global within that tenant |

Reads use `settings.effective_value(key, domain_id=None)`, which applies
the precedence: domain override → global value → builtin default.

### Catalog (Phase 1)

| Key                                    | Type   | Default          | Notes                                  |
|----------------------------------------|--------|------------------|----------------------------------------|
| `security.allow_http_lan`              | bool   | `True`           | Allow plain HTTP. Set `False` to require HTTPS. |
| `security.signing_key`                 | string | (auto-generated) | HMAC secret. Generated on first boot.  |
| `default_timezone`                     | string | `UTC`            | Server-wide fallback IANA TZ.          |
| `job.max_concurrent_image_transcodes`  | int    | tier-dependent   | Image transcode job concurrency.       |
| `job.max_concurrent_video_jobs`        | int    | tier-dependent   | Video job concurrency (Phase 2+).      |
| `sse.max_concurrent_connections`       | int    | tier-dependent   | Soft cap on display SSE connections.   |
| `heartbeat.batch_seconds`              | int    | `60`             | Display heartbeat flush interval.      |
| `upload.max_size_mb`                   | int    | tier-dependent   | Single-file upload cap.                |
| `cache.in_memory_mb`                   | int    | tier-dependent   | In-memory cache budget.                |
| `disk.upload_root`                     | string | `''` (use config) | Absolute or relative path for all tenant media (`d1/`, `d2/`, …). Superadmin only. When saving a new path, enable **Move existing tenant files** in the edit dialog to migrate data. API: `POST /api/system/upload-storage/migrate`. |
| *(per tenant)* `storage_root_path`   | —      | default `d{id}/` under upload root | Superadmin only on **Tenant Management** → Edit tenant → **Storage location**. Moves only that tenant's folder. API: `PUT /api/domains/<id>/storage` with `storage_root_path` and optional `move_existing`. |
| `disk.warn_pct`                        | int    | `80`             | Disk-usage warning threshold.          |
| `disk.block_uploads_pct`               | int    | `95`             | Disk-usage threshold to block uploads. |

Plus auto-detected reference values (always global, prefixed `auto.`):

| Key                              | Description                                  |
|----------------------------------|----------------------------------------------|
| `auto.detected_cpu_count`        | Effective CPU count (cgroup-aware).          |
| `auto.detected_ram_bytes`        | Total RAM in bytes (cgroup-aware).           |
| `auto.detected_free_disk_bytes`  | Free space on `UPLOAD_FOLDER`.               |
| `auto.detected_os`               | `'Linux'`, `'Windows'`, `'Darwin'`.          |
| `auto.detected_is_container`     | `True` inside Docker / Kubernetes.           |
| `auto.tier`                      | `'small'` / `'medium'` / `'large'`.          |
| `auto.<setting_key>`             | Recommended default for the detected tier.   |

The `auto.*` keys are read-only references — the admin overrides above sit
alongside them and take precedence.

### Tier classification

Done by `system_caps.tier()`:

| Tier   | CPU    | RAM     | Recommended displays |
|--------|--------|---------|----------------------|
| small  | ≤ 4    | ≤ 8 GB  | up to ~200           |
| medium | ≤ 16   | ≤ 32 GB | up to ~500           |
| large  | > 16   | > 32 GB | up to ~1000          |

Re-detection happens on every `bootstrap.run()` (every server start).
Manual overrides are not clobbered.

### Editing settings (CLI)

```python
from app import app
import settings

with app.app_context():
	# Global override
	settings.set('upload.max_size_mb', 250)

	# Per-tenant override
	settings.set('upload.max_size_mb', 500, domain_id=2)

	# Read effective value
	print(settings.effective_value('upload.max_size_mb', domain_id=2))  # 500
	print(settings.effective_value('upload.max_size_mb', domain_id=1))  # 250
```

### UI

Superadmins manage settings via **System Settings** (sidebar -> Admin
section). The page lists every key in `BUILTIN_DEFAULTS` plus any
auto.* / unknown rows present in the table, with three columns:

* **Default** -- the BUILTIN_DEFAULTS value
* **Global value** -- the row at `domain_id IS NULL` (bold when overridden)
* **Effective** -- what `effective_value()` returns at the active scope

A scope dropdown switches between global and per-domain overrides.
Domain admins (anyone with the `domain.admin` permission for a domain)
can edit overrides for that domain only; global edits remain superadmin.

Sensitive values (e.g. `security.signing_key`) are shown as `***` and are
only writable by superadmin. `auto.*` keys are surfaced for visibility
but are read-only.

Endpoints:

| Endpoint                              | Auth                              |
|---------------------------------------|-----------------------------------|
| `GET /api/settings`                   | superadmin (global scope)         |
| `GET /api/settings?domain_id=N`       | superadmin OR `domain.admin` of N |
| `PUT /api/settings/<key>`             | scope rules (body: `value`, `domain_id`) |
| `DELETE /api/settings/<key>?domain_id=N` | scope rules (revert override)  |

---

## Audit log

Every state-changing action writes one row to `audit_log`. Schema:

| Column                | Description                                   |
|-----------------------|-----------------------------------------------|
| `id`                  | autoincrement                                 |
| `timestamp`           | UTC timestamp                                 |
| `domain_id`           | tenant where the action happened (NULL if global) |
| `actor_user_id`       | user who initiated the action (NULL for system tasks) |
| `actor_api_token_id`  | API token used (NULL if session auth)         |
| `action`              | dot-form key, e.g. `'media.upload'`           |
| `target_type`         | `'media'` / `'display'` / etc.                |
| `target_id`           | string id of the target                       |
| `payload`             | JSON; sensitive keys auto-redacted to `'***'` |
| `ip_address`          | client IP (X-Forwarded-For honored)           |
| `user_agent`          | truncated to 255 chars                        |

### Reading

In the UI: **Audit Log** in the sidebar (requires `audit.read`, granted to
all four system roles). Filters include action key, target type, actor user id,
date range, and display-focused activity. Superadmins can view all tenants and
filter by tenant; regular users/admins remain scoped to their active tenant.

In code:
```python
from models import AuditLog
from tenant_filter import bypass_tenant_filter   # if cross-tenant view is needed

# Recent entries in current tenant
AuditLog.query.order_by(AuditLog.timestamp.desc()).limit(100).all()
```

### JSON API

| Endpoint                       | Returns                                  |
|--------------------------------|------------------------------------------|
| `GET /api/audit`               | Paginated list with filters              |
| `GET /api/audit/<id>`          | Single entry incl. full payload          |
| `GET /api/audit/actions`       | Distinct action keys (filter dropdown)   |

Permission to view: `audit.read` (granted to all four system roles).

---

## Proof of Play

Append-only playback evidence: one row per slide the player finishes (when
the feature is enabled). Rows are tenant-scoped on `proof_of_play.domain_id`.

### Settings (global)

| Key | Default | Meaning |
|-----|---------|---------|
| `proof_of_play.enabled` | `false` | Master switch — no rows recorded when off |
| `proof_of_play.retention_days` | `90` | Rows older than this are deleted by the daily sweep (`0` = no purge) |
| `proof_of_play.min_duration_ms` | `1000` | Ignore slides shorter than this |

Enable via **System Settings** or the superuser toggle on **Administration →
Proof of Play**. Tenant admins can view data but cannot enable/disable
recording or run purge.

### UI access

**Administration → Proof of Play** (requires `audit.read`).

| Role | Scope |
|------|--------|
| Superuser | All tenants (`?scope=all`) or filter `?domain_id=N`; defaults to **Active tenant** |
| Tenant admin | Only domains where they hold `domain.admin`; cannot pass `scope=all` or another tenant's `domain_id` |

### JSON API

| Endpoint | Auth | Notes |
|----------|------|-------|
| `GET /api/proof-of-play` | `audit.read` | List events; same query params as the UI filters |
| `GET /api/proof-of-play/filters` | `audit.read` | Distinct `item_type` and `plugin_key` values for dropdowns |
| `GET /api/proof-of-play.csv` | `audit.read` | CSV export |
| `POST /api/proof-of-play/purge` | superuser + `audit.read` | Manual retention sweep |
| `POST /api/proof-of-play/enable` | superuser + `audit.read` | Toggle `proof_of_play.enabled` |

Query parameters: `since`, `until`, `display_id` (repeatable), `item_type`,
`plugin_key`, `limit`, and for superusers `scope=all` and/or `domain_id`.

Player ingest (no session — display token only):

`POST /api/display/<token>/proof-of-play` — see [API.md](API.md).

---

## Display diagnostics

Display diagnostics are separate from the audit log. They are client-emitted
runtime logs used for troubleshooting playback, sync, and network behavior.

Enable diagnostics on a display only while investigating. When enabled, the
player can send console warnings/errors, sync calibration/drift events,
network/offline state changes, and player runtime events to the server.

UI access:
- Display detail page: recent logs for that display
- **Administration -> Display Diagnostics**: central view with tenant, display,
  group, level, search, and limit filters

Superusers can view diagnostics across tenants. Non-superusers only see logs
for displays in their active tenant.

### Retention

No automatic purge in Phase 1. To trim:
```sql
DELETE FROM audit_log WHERE timestamp < NOW() - INTERVAL '90 days';
```

### Sensitive-key redaction

Substrings (case-insensitive) auto-redacted in payload:
`password`, `passwd`, `secret`, `api_key`, `apikey`, `token`,
`unlock_pin`, `pin`, `authorization`, `cookie`, `session`,
`oauth_client_secret`, `signing_key`.

Add new substrings in `audit.SENSITIVE_KEYS`.

### Adding audit calls to new code

```python
from audit import audit

audit('widget.update', target_type='widget', target_id=str(w.id),
	  payload={'changes': diff_dict})
```

`audit()` never raises — a logging failure cannot break a request.

### Retention policy

Old `audit_log` rows are pruned by a periodic sweep (`audit_retention.py`).
Five settings keys (all under `audit.retention.*`) drive it:

| Key | Default | Meaning |
|-----|---------|---------|
| `audit.retention.enabled` | `true` | Master switch. When false, no rows are ever deleted. |
| `audit.retention.default_days` | `365` | Fallback max age. `0` = keep forever (only overrides prune). |
| `audit.retention.overrides` | `{}` | Per-action overrides as JSON. Trailing `.` = prefix match. `0` = keep forever for that action. |
| `audit.retention.purge_interval_hours` | `24` | Sweep frequency. Read once at startup; restart to change. |
| `audit.retention.batch_size` | `5000` | Max rows deleted per action per pass; remainder rolls to next sweep. |

Override examples:

```json
{
  "login.success":      30,
  "display.heartbeat":  7,
  "domain.":            1825,
  "audit.retention.":   90
}
```

Resolution order for one action: exact match → longest matching prefix →
`default_days`. The summary row written by each sweep itself
(`audit.retention.purge`) is exempt from pruning unless you opt it in via an
override.

Run a sweep on demand:

```
POST /api/audit/retention/purge        (superadmin only)
→ { "status": "success", "deleted_by_action": {...}, "total": N }
```

Or programmatically:

```python
import audit_retention
audit_retention.purge_now()   # safe, never raises; returns {action: count}
```

---

## Background jobs

Single-process in-memory runner. Two threads (default), one scheduler.

### Inspecting state

```python
import jobs

jobs.queue_size()     # pending one-shot jobs
jobs.periodic_jobs()  # [{name, every_s, next_run_in_s}, ...]
```

### Currently registered periodic jobs

| Name                | Default interval | Source         |
|---------------------|------------------|----------------|
| `heartbeat-flush`   | 60 s             | `heartbeat.install()` |

### Tuning concurrency

Worker count is fixed at `app.py` startup (`jobs.start(app, worker_count=N)`).
To change, edit `app.py` or scale the value with `auto.detected_cpu_count`
read from settings. A per-deployment override via env var lands in Phase 2.

### Phase 2+

When you outgrow a single process (multiple gunicorn workers, multiple
machines), replace `jobs.py` with a Celery / RQ / dramatiq adapter that
implements `start`, `submit`, `schedule_periodic` with the same signatures.
Caller code (`heartbeat.py`, future jobs) won't change.

---

## Storage

Layout (default under `UPLOAD_FOLDER` or `disk.upload_root`):

```
<upload_root>/
  d<id>/
	images/<uuid>.<ext>
	videos/<uuid>.<ext>
	thumbnails/<uuid>.png
	misc/...
```

Media rows always store **relative** paths such as `d2/images/<uuid>.jpg`.
The server resolves them to absolute paths via `upload_paths.resolve_tenant_root()`
and `storage.py`, so a tenant can use a custom filesystem root without changing
database paths.

### Global upload root (`disk.upload_root`)

Superadmin only. **Administration → System Settings** → `disk.upload_root`.

| | |
|---|---|
| Default | `UPLOAD_FOLDER` from `config.py` (usually `server/uploads/`) |
| Purpose | Move **all** tenants to another drive or directory |
| On save | Check **Move existing tenant files** to copy/move `d1/`, `d2/`, … into the new root |
| Skip policy | Files that already exist at the destination path are skipped |

API:

```
PUT /api/settings/disk.upload_root
  body: { "value": "D:\\Signage\\uploads", "move_existing": true }

POST /api/system/upload-storage/migrate
  body: { "destination": "D:\\Signage\\uploads", "move": true, "dry_run": false,
          "apply_setting": false }
```

Audit action: `upload_storage.migrate` (global).

### Per-tenant storage path (`Domain.storage_root_path`)

Superadmin only. **Administration → Tenant Management** → **Edit** → **Storage location**.

| | |
|---|---|
| Default | `<upload_root>/d<id>/` |
| Purpose | Move **one** tenant to another folder (e.g. large tenant on a fast disk) |
| Browse | **Browse server folders** — lists drives/directories on the **server** (not the admin PC’s file picker) |
| On save | **Save storage location** (separate from the main tenant **Save** button). Check **Move this tenant's files** to migrate. |
| Clear path | Leave custom path blank → reverts to default folder; files are **not** moved back automatically |

API:

```
GET  /api/domains/<id>/storage
PUT  /api/domains/<id>/storage
  body: { "storage_root_path": "D:\\Media\\tenant-2", "move_existing": true }

GET  /api/system/path-browser?path=...   # superadmin; list server directories
```

Audit actions: `upload_storage.migrate_tenant`, `domain.storage.update`.

> **Logo upload vs storage path:** Logo **Choose file** uploads from your computer
> into the tenant folder. Storage path tells the server where that tenant’s
> entire media tree lives on disk — the browser cannot use your PC’s folder dialog
> for that (security). If AISignX runs on the same machine you admin from, server
> drives (`C:`, `D:`, …) are your local disks.

### Quota

Set via `Domain.storage_quota_bytes` (NULL = unlimited). Enforced at the
ORM layer by a `before_flush` listener — an insert that would push the
tenant over quota raises `RuntimeError` and the transaction rolls back.

### Recomputing usage

If `Domain.storage_used_bytes` drifts from disk reality (manual file
deletion, restore from backup):

```python
from storage import recompute_used
recompute_used(domain_id)
```

### Disk-space monitoring

`disk_monitor.py` runs `shutil.disk_usage()` on the upload partition
every 5 minutes. The latest snapshot is mirrored into these auto.* keys
(visible from the settings UI / API):

| Key                       | Type | Meaning                            |
|---------------------------|------|------------------------------------|
| `auto.disk_total_bytes`   | int  | Total partition size               |
| `auto.disk_used_bytes`    | int  | Used bytes                         |
| `auto.disk_free_bytes`    | int  | Free bytes                         |
| `auto.disk_used_pct`      | int  | Used percentage (rounded to 0.01)  |
| `auto.disk_probed_at`     | int  | Unix epoch of the last probe       |

Thresholds (system settings, with sensible defaults):
- `disk.warn_pct` — default 80; threshold for the warning audit entry
- `disk.block_uploads_pct` — default 95; uploads are refused above this

Threshold transitions emit one audit entry each:
- `disk.warn_threshold_crossed` / `..._recovered`
- `disk.block_threshold_crossed` / `..._recovered`

Enforcement is centralized in `storage.check_quota()`, so all upload
paths (`/api/media`, `/api/media/from_url`, `/api/media/<id>/replace`,
`/api/media/<id>/replace_by_url`) receive the same error response.

Inspect status:

```
GET /api/system/disk     # superadmin only
```

Returns `{snapshot, warn_pct, block_pct, blocking}` so the admin UI can
surface a banner when uploads are disabled.

---

## Signed media URLs

The `/uploads/<path>` route gates access on `dN/`-prefixed paths via four
mechanisms (first match wins):

1. **HMAC signature** in `?e=<expiry>&sig=<base64>` query string. Used by
   display players (no cookies). Signed by `storage.signed_url(rel_path)`,
   default TTL 1 hour, secret = `security.signing_key`.
2. **Authenticated session** whose tenant matches the file's tenant. Used
   by the admin UI's `<img>` tags.
3. **Superadmin session** can read any tenant's files.
4. **Legacy paths** (no `dN/` prefix, from pre-Phase-1 layouts) served
   unconditionally for back-compat.

Anything else returns 403. Issuing a signed URL:

```python
import storage

# In a request handler (so url_for has app context):
url = storage.signed_url(media.file_path, ttl_seconds=3600, external=True)
```

The TTL constant lives in `storage.SIGNED_URL_TTL_DEFAULT`.

**If `security.signing_key` is rotated**, all outstanding signed URLs
invalidate immediately. Players will re-fetch the playlist on the next
SSE reload event and pick up new URLs.

---

## Rate limiting

`rate_limit.py` is an in-process token-bucket limiter. No Redis or external
service required; thread-safe via a single global lock.

### Algorithm

Each bucket holds `(tokens, last_refill_ts, last_audit_minute)`. On every
gated request the bucket is refilled at `limit / window_s` tokens per
second up to `limit`, then 1 token is deducted. If `tokens < 1`, the
request is refused with HTTP 429 and a `Retry-After` header.

Stale buckets (untouched for 5 minutes) are swept lazily — the sweep
runs at most every 30 s and only when the live count exceeds
`_MAX_BUCKETS / 2`. Hard cap is 50,000 buckets (~4MB).

### Protected endpoints

| Endpoint                          | Default limit | Bucket key |
|-----------------------------------|---------------|------------|
| `POST /login`                     | 5/min/IP      | `ip:<addr>:login` |
| `POST /api/register`              | 10/min/IP     | `ip:<addr>:register` |
| `POST /api/register/browser`      | 5/min/IP      | `ip:<addr>:browser_register` |
| Any `@api_auth_required` route, token caller | 600/min/token | `tok:<id>:api` |
| Any `@api_auth_required` route, session caller | 600/min/IP | `ip:<addr>:api` |

The generic API limit prefers the token id over IP for token callers so
a NAT'd fleet of legitimate displays isn't co-limited with each other.

### Settings keys

| Key                                  | Type | Default | Effect |
|--------------------------------------|------|---------|--------|
| `ratelimit.enabled`                  | bool | true    | Master switch. Disable with extreme care. |
| `ratelimit.login_per_min`            | int  | 5       | `/login` POST per IP |
| `ratelimit.register_per_min`         | int  | 10      | `/api/register` POST per IP |
| `ratelimit.browser_register_per_min` | int  | 5       | Browser self-registration per IP |
| `ratelimit.api_per_min`              | int  | 600     | All `@api_auth_required` routes |

Edit live through the System Settings UI (sidebar -> Admin -> System
Settings). Settings are cached in the limiter for 30 s; force a refresh
with `rate_limit.invalidate_settings_cache()` if needed.

### Failure modes

- **Misconfigured limit (≤0)**: fail-open. Better than locking everyone out.
- **Audit failure during refusal**: silently ignored. The 429 still
  returns; the limiter never depends on audit success.
- **Settings unreadable** (boot ordering / DB hiccup): falls back to the
  built-in defaults above.

### Audit

First refusal per `(bucket_key, minute)` emits one
`rate_limit.exceeded` audit entry. Subsequent refusals in the same
minute are silently dropped to avoid log floods.

### Adding a new limited endpoint

```python
from rate_limit import limit_per_ip, limit_per_token

# IP-keyed -- best for unauthenticated routes
@my_bp.route('/api/foo', methods=['POST'])
@limit_per_ip('foo', limit=20, window_s=60)
def api_foo(): ...

# Token-keyed -- best for authenticated APIs
@my_bp.route('/api/bar', methods=['GET'])
@api_auth_required(['bar:read'])
@limit_per_token('bar', limit=100, window_s=60)
def api_bar(): ...

# Settings-driven -- override `limit` via a system-settings key
@limit_per_ip('foo', settings_key='ratelimit.foo_per_min')
def api_foo(): ...
```

If you back the limit with a setting, also add the key to
`settings.py:BUILTIN_DEFAULTS` so admins can see and edit it.

---

## Tenant-leak scanner

Static check that flags suspicious patterns in the codebase:

```bash
python -m tools.audit_tenant_leaks
```

Exit code `0` → clean. Non-zero → CI failure.

Patterns flagged:

| Code           | Meaning                                             |
|----------------|-----------------------------------------------------|
| `BYPASS`       | `bypass_tenant_filter()` outside whitelist          |
| `RAW-SQL`      | `session.execute(text(...))` (skips tenant filter)  |
| `HARDCODED`    | `domain_id=N` literal in code (not a Column / param) |
| `CROSS-DOMAIN` | `Domain.query.*` (needs superadmin justification)   |

Silence a single line by adding `# tenant-ok: <reason>`. Whitelist a whole
file by adding it to `WHITELIST_FILES` in
`tools/audit_tenant_leaks.py`.

Add to CI:

```yaml
- name: Tenant-leak scan
  run: python -m tools.audit_tenant_leaks
```

---

## Backup and restore

Backups bundle the SQLite database, the per-tenant uploads tree, and
the plugins tree into a single `.zip` written to `./backups/`. The
directory is intentionally outside the static and uploads webroots so
backup files can never be served by public file routes.

### Creating a backup

Sidebar → Admin → Backups → **Create Backup** (superadmin only). The
DB snapshot uses `sqlite3.Connection.backup()` so no downtime is
required; uploads and plugins are copied while the server runs.

API:
```
POST /api/backups
  body: {include_uploads: bool, include_plugins: bool}
```

### Backup format

Each backup is a zip with this layout:
```
manifest.json                metadata + integrity hash
db/digital_signage.db        sqlite snapshot
uploads/...                  optional
plugins/...                  optional
```

`manifest.json` includes:
- `schema_version` — currently 1
- `created_at` — ISO 8601 UTC
- `app_version` — best-effort
- `contents` — `{db, uploads, plugins}` flags
- `counts` — users, domains, displays, media, playlists, schedules
- `sqlite_page_size` / `sqlite_page_count` — sanity check at restore
- `db_sha256` — integrity check
- `counts.uploads_files` / `counts.plugins_files` — file counts

### Restoring

> **Restore is destructive.** The current DB, uploads, and plugins are
> moved to `<dir>.restore-<ts>` sibling directories and replaced. The
> server **must be restarted** for the new database to take effect.
> Uploads/plugins changes are picked up on the next request.

UI: pick a backup → **Restore** → check the trees you want to roll
back → type the filename to confirm → submit.

API:
```
POST /api/backups/<filename>/restore
  body: {confirm: true,
         confirm_filename: '<exact filename>',
         restore_uploads: bool,
         restore_plugins: bool}
```

The restore endpoint refuses unless `confirm=true` AND
`confirm_filename` matches the path parameter exactly. This prevents a
stray click from rolling the entire server back.

### Recovery from a failed restore

If the extraction phase fails partway through, the staging directories
(named `instance/digital_signage.db.restore-<ts>`,
`uploads.restore-<ts>`, etc.) are left in place. To roll back:

1. Stop the server.
2. Move the half-extracted target out of the way.
3. Rename the staging dir back to its original name.
4. Restart.

The API response always includes `staged_paths` so the admin can copy
them straight from the audit log if needed.

### Why a server restart is required after a DB restore

SQLAlchemy holds an open connection pool. The restore code disposes
the pool before renaming the file (without `db.engine.dispose()` the
rename fails on Windows with `WinError 32`), but the live process is
still configured against the connection string — the safest path
forward is a clean restart so all in-memory caches and worker threads
re-bind against the new file.

### Security notes

- Backups contain hashed credentials, the signing-key secret used for
  signed media URLs, and all tenant media. Treat the `.zip` as
  highly sensitive — encrypt at rest, restrict shell access to the
  `backups/` directory.
- All endpoints require **superadmin**. Domain admins cannot create or
  restore backups (a backup is cross-tenant by definition).
- Filename validation in `backup._safe_filename()` rejects any name
  that doesn't match `aisignx-backup-*.zip` and any path-traversal
  characters (`/`, `\`, `..`).

### Audit actions

`backup.create`, `backup.download`, `backup.delete`, `backup.restore`
(with `staged_paths` in the payload).

---

## First-boot bootstrap

`bootstrap.run(app)` is called on every server start (idempotent). It:

1. Detects hardware, classifies tier, writes `auto.*` settings
2. Generates `security.signing_key` (32 bytes hex) — once, never overwritten
3. Sets `default_timezone` from `tzlocal` if available — once
4. Backfills any missing `Permission` rows (so new perms added in code
   land without a separate migration)
5. Creates / re-syncs the four system roles' permission lists
6. Creates the `Default` domain if no domains exist
7. Assigns the first existing user the `domain_admin` role in the Default
   domain (bootstraps the seeded `admin` account)

To skip (e.g. during Alembic migration generation):
```bash
AISIGNX_SKIP_INIT_DB=1 flask db migrate ...
```

---

## Common operator tasks

**Reset a forgotten admin password**
```python
from app import app
from models import User, db
from tenant_filter import bypass_tenant_filter

with app.app_context(), bypass_tenant_filter():
	u = User.query.filter_by(username='admin').first()
	u.set_password('NewSecret!42')
	db.session.commit()
```

**Make an existing user a superadmin**
```python
with app.app_context(), bypass_tenant_filter():
	u = User.query.filter_by(username='alice').first()
	u.is_superadmin = True
	db.session.commit()
```

**Grant a user a role in a domain**
```python
from models import User, Role, Domain, UserDomainRole

with app.app_context(), bypass_tenant_filter():
	u = User.query.filter_by(username='bob').first()
	d = Domain.query.filter_by(slug='acme').first()
	r = Role.query.filter_by(name='content_editor', domain_id=None).first()
	db.session.add(UserDomainRole(user_id=u.id, domain_id=d.id, role_id=r.id))
	db.session.commit()
```
