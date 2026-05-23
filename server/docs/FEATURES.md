# AISignX — Feature Catalog

This document is the single-page tour of what the system can do today and
what is still on the roadmap. Each section gives a short "what it is",
the key options, and (where useful) a pointer to a deeper doc.

> Legend
> - ✅ Shipped and exposed in the UI
> - 🟡 Shipped in the backend; UI/discoverability still rough
> - 🔲 Not started / planned

---

## 1. Core platform

| Capability | Status | Notes |
|---|---|---|
| Single-server Flask app, SQLite (Postgres-ready) | ✅ | `app.py`, `models.py` |
| Multi-tenant admin UI ("Tenants"; internal model remains `Domain`) with tenant-scoped queries | ✅ | `tenant_filter.py`; superadmin can see/filter across tenants |
| Role / permission system including custom tenant roles | ✅ | `permissions.py`, decorators on every route |
| API tokens with scoped permissions | ✅ | `docs/API.md` |
| Audit log (who/what/when, JSON payloads) | ✅ | `audit_views.py`, `templates/audit.html` |
| Audit log **download** (CSV / JSON, filtered, ≤50k rows) | ✅ | `/api/audit/export?format=csv|json` |
| Audit retention sweep (configurable per tenant) | ✅ | `audit_retention.py`, daily job |
| Idempotent schema evolution at boot (no Alembic required) | ✅ | `bootstrap.py` |
| Background job runner (heartbeat flush, alerts, sweeps) | ✅ | `jobs.py` |
| System health / self-test page | ✅ | Admin → System Health |
| Public health probes | ✅ | `/healthz`, `/readyz` |

---

## 2. Media library

| Capability | Status | Notes |
|---|---|---|
| Upload images / video / webpages | ✅ | `media.py`, `templates/media.html` |
| **Multi-file upload** (pick many in one dialog) | ✅ | Uploaded one-by-one client-side so per-file dedupe / quota errors are surfaced |
| URL ingestion (webpage as media) | ✅ | |
| Replace media file or URL in place | ✅ | Keeps playlist references intact |
| Per-tenant disk quota | ✅ | Enforced on every upload |
| Global upload root override (`disk.upload_root`) | ✅ | Superadmin; optional migrate all `dN/` folders |
| Per-tenant storage path (`storage_root_path`) | ✅ | Superadmin; Tenant Management + migrate one tenant |
| Server folder browser for storage paths | ✅ | `GET /api/system/path-browser` |
| **SHA-256 dedupe** with "upload as separate copy" prompt | ✅ | `allow_duplicate=1` to override |
| Thumbnails (image + video frame) with status badge | ✅ | `thumbnail_utils.py`; per-item regenerate button |
| **Bulk selection** in the library | ✅ | Sticky action bar |
| **Bulk delete** (skips items in use unless `force`) | ✅ | `/api/media/bulk-delete` |
| **Bulk set duration** (prompt-based) | ✅ | |
| **Use detected video length** single and bulk reset | ✅ | ffprobe metadata via `media_duration.py` |
| Safe delete: refuses when referenced by a playlist | ✅ | `force=1` to override |
| **Maintenance modal**: orphan scan + cleanup of stray files | ✅ | `/api/media/orphans`, server-side prune |
| Image variants / responsive serving | 🟡 | Schema present (`Media.variants`); pipeline planned for Phase 2 |
| Bulk rename (find/replace + prefix/suffix) | ✅ | `/api/media/bulk-rename` |
| Bulk tag (add / remove / replace) | ✅ | `/api/media/bulk-tag`, `/api/media/<id>/tags`, `/api/media/tags` |
| Per-item tag chips + tag filter / cloud | ✅ | Media library row + sidebar filter |
| Folders | ✅ | Folder sidebar, move operations, and filtered browsing |

See `docs/PLAYLISTS_MEDIA.md` for the deeper reference.

---

## 3. Playlists

| Capability | Status | Notes |
|---|---|---|
| Playlist CRUD | ✅ | `playlists.py`, `templates/playlists.html` |
| Drag-and-drop item reordering | ✅ | Sortable.js; "Save New Order" button |
| Add image / video / webpage / plugin items | ✅ | Tabbed picker in Add Item modal |
| **Multi-add**: select many media items, add in one click | ✅ | Add Item modal footer; sets default duration + transition |
| Per-item duration | ✅ | `0` means full detected video or clip range for videos |
| **Video clip range** (start / end seconds) | ✅ | Edit Item modal |
| Per-item aspect mode override (`fit` / `fill` / `stretch` / `center`) | ✅ | |
| Per-item mute audio | ✅ | |
| **Per-item transition** (`cut`, `fade`, `crossfade`, `wipe`) | ✅ | "Inherit / None" defaults to playlist setting |
| **Playlist-wide default transition** | ✅ | Auto-saving selector at top of editor |
| **Random transition per slide** | ✅ | Playlist default = `random`; player shuffles `fade` / `crossfade` / `wipe` |
| **Item multi-select bulk actions** | ✅ | Set transition, set duration, use video length, toggle mute, delete (with renumber) |
| Playlist copy / clone | ✅ | `/api/playlists/<id>/copy` |
| Plugin items (configured per item) | ✅ | Plugin Item Modal; sandbox attrs resolved per tenant |
| Fallback playlist (if primary fails to resolve) | 🟡 | Column exists, runtime fallback in Phase 2 |
| Smart playlists / rules-based | ✅ | Tag/type/name rules + order + limit; resolved live from Media at play time. **Playlist editor → Smart playlist rules** |

---

## 4. Schedules

| Capability | Status | Notes |
|---|---|---|
| Per-display or per-group schedule | ✅ | `schedules.py`, `docs/SCHEDULES.md` |
| Date range, time of day, days of week | ✅ | |
| Per-schedule timezone (IANA) with tenant default fallback | ✅ | |
| Priority resolution (highest priority wins) | ✅ | |
| Activate / deactivate without delete | ✅ | |
| Filter and bulk edit schedules | ✅ | Search, target/playlist/status/day filters plus bulk edit modal |
| Conflict / overlap visualisation | ✅ | **Admin → Schedule Conflicts** (`/admin/schedule-conflicts`) |

---

## 5. Displays

| Capability | Status | Notes |
|---|---|---|
| Display registration with pairing code | ✅ | `docs/DISPLAYS.md` |
| Per-display resolution, orientation, aspect mode | ✅ | |
| Display groups (assign schedules to many at once) | ✅ | |
| **Synchronised playback** within a group (wall-clock anchored) | ✅ | `sync_playback.py` |
| Server-time calibration and drift recovery | ✅ | Multi-sample calibration, watchdog recovery, plugin clock offset |
| Heartbeat + last-seen tracking, app version reporting | ✅ | `display.app_version` evolved at boot |
| Display issue badges | ✅ | Offline, no version, version drift, alert active/snoozed |
| Display diagnostics | ✅ | Per-display opt-in client logs + central diagnostics viewer |
| **Display alerts** (offline detection, digest emails) | ✅ | Per-tenant thresholds; periodic + hourly digest jobs |
| Remote command channel (reload, reboot, screenshot, …) | ✅ | `docs/CLIENT_COMMAND_PROTOCOL.md` |
| **Proof-of-Play** (per-slide playback records, batched) | ✅ | Per-tenant; superuser sees all, tenant admin sees own tenant(s) — [OPERATIONS.md](OPERATIONS.md) |
| Per-display capability filtering (max video height etc.) | ✅ | Items unrenderable on a display are excluded with a reason |
| Offline-first behaviour, cached last playlist/media | ✅ | Service worker for browser/Electron; Android WebCache for WebView media |
| Emergency-broadcast lock (sticky until explicit clear) | ✅ | `docs/EMERGENCY_BROADCAST.md` |
| Per-display screenshot history viewer | 🟡 | Backend present; UI minimal |

---

## 6. Player runtime (browser / kiosk / Electron / Android)

| Capability | Status | Notes |
|---|---|---|
| Image, video, webpage, plugin slide rendering | ✅ | `static/js/display_player.js` |
| Slide preloading | ✅ | Reduces black frames between items |
| Transitions: `cut` / `fade` / `crossfade` / `wipe` / slides / zoom / spin / flip / iris / puzzle / random | ✅ | CSS-driven; `cut` skips the transition entirely |
| Aspect modes: fit / fill / stretch / center | ✅ | Per-display default, per-item override |
| Plugin sandbox + `allow=` capability gating | ✅ | Computed from tenant policy |
| SSE-driven live update of playlist / emergency / commands | ✅ | Auto-reconnect; offline lock preserved |
| Pause / scrub / skip via remote command | ✅ | Plugins receive `signage:pause` / `signage:resume` postMessage |
| Video autoplay-blocked recovery (advance) | ✅ | |
| Idle cursor hiding on web player | ✅ | Cursor shows while active and hides after idle |
| Android video placeholder mitigation | ✅ | Black poster/pending-video masking in player |
| Audio output routing (Windows / Android) | 🟡 | Best-effort via OS defaults |
| Hardware-accelerated video on low-power devices | 🟡 | Depends on client (Electron flags, Android WebView) |

---

## 7. Plugins

| Capability | Status | Notes |
|---|---|---|
| Plugin registry + per-tenant enable/disable | ✅ | `plugin_system.py`, `docs/PLUGINS.md` |
| Per-plugin granted permissions → iframe sandbox attrs | ✅ | Editable in **Admin → Plugin Policy** |
| **CSP origin pinning** per plugin (`csp_origins` in `plugin.json`) | ✅ | Locked-down `Content-Security-Policy` on the runner; surfaced in the policy admin UI |
| **Live policy push** to displays (SSE `plugin_policy` event) | ✅ | Iframes re-init via `signage:plugin_policy` postMessage and reload to re-evaluate sandbox/CSP |
| Plugin manifest signing + verification | ✅ | `plugin_signing.py`; signature status shown per plugin |
| Built-in plugins: clock, stocks, YouTube | ✅ | `plugins/<name>/` |
| Hot-reload of plugin config from the editor | ✅ | |
| Marketplace / external install | 🔲 | |

---

## 8. Clients

| Client | Status | Notes |
|---|---|---|
| Browser (any modern Chromium / Firefox) | ✅ | `docs/BROWSER_ACCESS.md` |
| Electron desktop wrapper | ✅ | `clients/electron-client/`, `docs/CLIENTS.md` |
| Windows packaged client / service | ✅ | `docs/WINDOWS_CLIENT_PACKAGING.md` |
| Android WebView client | ✅ | `clients/android-client/`; Device Owner receiver supports unattended APK updates |
| Native iOS / tvOS | 🔲 | |

---

## 9. Deployment & ops

| Capability | Status | Notes |
|---|---|---|
| Single-binary launch (`python app.py`) | ✅ | |
| Windows production deployment guide | ✅ | `docs/DEPLOY_WINDOWS.md`, `docs/PRODUCTION_DEPLOYMENT.md` |
| Linux deployment guide | ✅ | `docs/DEPLOY_LINUX.md` |
| HTTP-only or HTTPS-only modes | ✅ | `docs/SERVER_HTTP_ONLY_or_HTTPS_ONLY_Version2.md` |
| Reverse-proxy aware (X-Forwarded-* trust) | ✅ | Configurable hop count |
| Backup / restore tooling | ✅ | **Admin → Backups**: create, download, delete, restore (with typed-filename confirmation) |
| Postgres support | 🟡 | Models are portable; SQLite-only today. See **Database backend** below for trigger criteria. |
| Auto-update of clients | ✅ | Electron silent update; Android unattended install when Device Owner |

---

## 10. Security & compliance

| Capability | Status | Notes |
|---|---|---|
| Login, password reset, session cookies | ✅ | |
| MFA / TOTP | 🔲 | |
| SSO (OIDC / SAML) | 🔲 | |
| Per-tenant API token scopes | ✅ | |
| Signed media URLs (no cookies needed by player) | ✅ | `storage.signed_url()` |
| CSRF protection on form posts | ✅ | |
| Tenant isolation tested by leak audit tool | ✅ | `tools/audit_tenant_leaks.py` |

---

## Quick "what do I open?" map

| You want to… | Go to |
|---|---|
| Upload many files at once | **Media → Upload Media**, pick multiple files |
| Delete a bunch of media | **Media** → tick checkboxes → **Delete selected** |
| Reset videos back to detected length | **Media** or **Playlist item** bulk bar → **Use video length** |
| Find / clean stray files on disk | **Media → Maintenance** |
| Add 12 photos to a slideshow | **Playlist → Add Item**, tick all, **Add selected** |
| Make slides cross-fade | **Playlist** → top selector "Default transition" → **Crossfade** |
| Vary the transition every slide | Default transition → **Random (per slide)** |
| Override transition for one slide | Edit that item, pick a transition (Inherit = use playlist default) |
| Download an audit trail for an incident | **Audit** → set filters → **CSV** or **JSON** |
| Move all tenant media to another drive | **Administration → System Settings** → `disk.upload_root` |
| Move one tenant to its own disk/folder | **Administration → Tenant Management** → Edit → **Storage location** |
| See why a display dropped off | **Displays** → row badge, Display Diagnostics, then alerts log |

---

## Roadmap (currently 🔲 above)

1. ~~Bulk rename / tag for media; folder organisation~~ 🟡 — bulk rename + tag shipped; folders still planned
2. Image variant pipeline (responsive serving by display capability)
3. ~~Schedule conflict visualiser~~ ✅ — **Admin → Schedule Conflicts**
4. ~~Smart / rules-based playlists~~ ✅ — **Playlist editor → Smart playlist rules**
5. Plugin marketplace + external install flow
6. MFA + SSO (OIDC, SAML)
7. Native iOS / tvOS client
8. ~~Electron auto-update channel~~ ✅ — Electron silent update and Android Device Owner auto-update are implemented
9. ~~UI for backup / restore~~ ✅ — **Admin → Backups** (Postgres migration UI still planned)
10. Per-display screenshot history browser
11. PostgreSQL backend (deferred — see **Database backend** below)

---

## Database backend

Currently **SQLite-only**. The ORM (SQLAlchemy) is portable and `bootstrap.py`'s
schema-evolution path uses `inspect()` so most DDL is dialect-agnostic, but the
following tie us to SQLite today:

- `backup.py` uses `sqlite3.backup()` for the online snapshot (would need a `pg_dump` path).
- A handful of `ADD COLUMN ... NOT NULL DEFAULT ...` migrations need rewrites for Postgres.
- No connection pool tuning for a remote DB.

**Trigger criteria for the Postgres migration** — do it when *any* are true:

- More than ~5 concurrent admin users with frequent writes.
- Multi-host app deployment (load-balanced workers / HA).
- A single tenant approaching ~1M audit + proof-of-play rows.
- A customer requirement for managed RDBMS (RDS / Cloud SQL / Azure DB).

Until then SQLite is faster for this workload (no network hop, single-writer
contention is low) and the on-line backup tool gives us point-in-time copies.
