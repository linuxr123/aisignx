# Multi-Tenancy Architecture (Phase 1)

This document explains how AISignX isolates data between tenants and how to
write new code that respects those boundaries. Read this before adding
routes, models, or background jobs.

---

## Concepts

**Domain** — the internal model/API name for the unit of tenancy. User-facing
UI should call this a **Tenant**. Every install has at least one (`Default`).
A domain/tenant owns its own:
- displays, display groups, schedules, playlists
- media files (on disk under `uploads/d<id>/`)
- API tokens
- emergency broadcasts and templates
- proof-of-play (playback evidence) rows
- audit log entries
- per-domain setting overrides

**User** — global, NOT scoped to a domain. The same user account can hold
different roles in different domains (`UserDomainRole`).

**Permission** — a single dot-form capability (e.g. `media.upload`,
`display.lockdown`). Defined in `permissions.PERMISSIONS`.

**Role** — a named bundle of permissions. Four are seeded by `bootstrap.py`:
`domain_admin`, `content_editor`, `display_operator`, `viewer`. New
custom roles can be added per-domain by storing them with `domain_id != NULL`.

**Superadmin** — `User.is_superadmin = True`. Cross-tenant operator that can
see and filter across all tenants, manage tenants, and manage users. There is always at least one (the bootstrap
`admin` account).

---

## Request lifecycle

1. **`@app.before_request`** (`app.py`) resolves the active tenant:
   - First tries `session['current_domain_id']` (web users; set by the
	 tenant switcher dropdown — implemented in `domains.py`, or on login via
	 `user_accounts.login_session_domain_id()`)
   - If the session has no tenant yet, picks a default for the logged-in user:
	 - **Superadmin** → the seeded **Default** tenant (`slug=default`, see
	   `bootstrap.default_tenant_domain_id()`)
	 - **Other users** → first tenant where they hold `domain.admin`,
	   `emergency.manage`, or `emergency.use`; otherwise their earliest role
	   assignment
   - For API requests, `utils.api_auth_required` later sets the tenant from
	 the API token's `domain_id`
   - Calls `tenant_filter.set_current_domain_id(did)` which stores `did` in
	 `flask.g`

2. **ORM queries** are auto-filtered by `tenant_filter.install_tenant_filter()`,
   which registers a SQLAlchemy `do_orm_execute` event listener. Any SELECT
   on a `TenantModel` subclass gets a
   `WHERE domain_id = current_domain_id()` clause appended via
   `with_loader_criteria`. **Default deny**: if no tenant context is set,
   queries return nothing (`domain_id = -1`).

3. **ORM inserts** are stamped by a `before_flush` listener:
   - If `domain_id` is unset → stamped with `current_domain_id()`
   - If `domain_id` is set to a different tenant → `RuntimeError` (cross-tenant
	 write blocked)
   - If `domain_id` is unset AND no tenant context → `RuntimeError`

4. **`@app.teardown_request`** clears the tenant context.

---

## Bypassing the filter

Some code legitimately needs cross-tenant access:
- superadmin domain listing
- bootstrap / housekeeping tasks
- API-token lookup before tenant context is known
- background jobs that aggregate across tenants

Use the `bypass_tenant_filter()` context manager:

```python
from tenant_filter import bypass_tenant_filter

with bypass_tenant_filter():       # tenant-ok: superadmin domain listing
	all_domains = Domain.query.all()
```

The `# tenant-ok: <reason>` comment silences the static leak scanner
(`tools/audit_tenant_leaks.py`). Whitelisted files (framework code in
`tenant_filter.py`, `bootstrap.py`, `audit.py`, `storage.py`, `admin.py`,
`utils.py`, `app.py`) don't need the comment.

---

## Adding a new tenant-scoped model

```python
from models import db, BaseModel, TenantModel

class Widget(db.Model, TenantModel):
	__tablename__ = 'widget'
	id   = db.Column(db.Integer, primary_key=True)
	name = db.Column(db.String(255), nullable=False)
	# domain_id is provided by TenantModel; do NOT redeclare it.
```

That's it. The query filter, insert auto-stamp, and per-tenant indexes are
inherited automatically. Add an Alembic migration with
`alembic revision --autogenerate`.

---

## Adding a new permission

1. Add the key to `permissions.PERMISSIONS` (single source of truth):
   ```python
   'widget.edit': 'Edit widgets',
   ```
2. Decide which system roles should hold it; add to `permissions.SYSTEM_ROLES`.
3. Restart the app — `bootstrap.py` runs on every start and backfills any
   missing permission rows + reapplies system role permission lists. No
   migration needed.
4. Decorate the route:
   ```python
   from permissions import require_permission

   @widget_bp.route('/api/widgets/<int:wid>', methods=['PUT'])
   @require_permission('widget.edit')
   def update_widget(wid):
	   ...
   ```

---

## Adding storage to a feature

Use `storage.py`, never `os.path.join(UPLOAD_FOLDER, ...)` directly:

```python
import storage

# Save an uploaded file
stored = storage.save_upload(file_storage, kind='image')
media.file_path = stored.rel_path        # 'd1/images/abcdef.png'
media.file_size = stored.size

# Reserve a path for a tool to write into
abs_p, rel_p = storage.reserve_path('thumbnail', '.png')
ffmpeg_make_thumbnail(input_abs, abs_p)
media.thumbnail_path = rel_p

# Delete
storage.delete(media.file_path)
```

Storage accounting (`Domain.storage_used_bytes`) updates automatically via a
`before_flush` listener. Quota enforcement happens at the same point — an
insert that would push the tenant over `Domain.storage_quota_bytes` raises
`RuntimeError` and the transaction rolls back.

---

## Audit logging

Call `audit()` on every state-changing action:

```python
from audit import audit

audit('media.delete', target_type='media', target_id=str(m.id),
	  payload={'name': m.name, 'size': m.file_size})
```

`audit()` never raises and auto-redacts payload fields containing common
sensitive substrings (`password`, `token`, `secret`, `pin`, …). Reads happen
through `permissions.has_permission(..., 'audit.read')`.

---

## Background jobs

Don't spawn raw `threading.Thread` objects from request handlers. Use the
shared runner so jobs share a worker pool, run inside an app context, and
report back via the periodic-job table.

```python
import jobs

# One-shot
jobs.submit(send_welcome_email, user.id)

# Periodic
jobs.schedule_periodic(my_cleanup_task, every_s=300, name='widget-cleanup')
```

See also `heartbeat.py` for an example of a high-frequency write path that
batches into one COMMIT per minute.

---

## CI

`python -m tools.audit_tenant_leaks` returns non-zero exit code if it finds
unwhitelisted patterns: bypass usage outside framework files, raw SQL,
hardcoded `domain_id=N` literals, or `Domain.query.*` outside whitelisted
files. Add a CI step that runs this on every PR.

---

## Tenant management

Superadmins manage tenants via the **Tenants** page in the UI. Internal route
and model names still use `Domain` unless a deliberate schema migration is
performed. The page calls these endpoints:

| Endpoint                       | Auth                          |
|--------------------------------|-------------------------------|
| `GET /api/domains`             | superadmin                    |
| `POST /api/domains`            | superadmin                    |
| `GET /api/domains/<id>`        | superadmin OR domain member   |
| `PUT /api/domains/<id>`        | superadmin OR `domain.admin`  |
| `DELETE /api/domains/<id>`     | superadmin                    |

`DELETE` refuses non-empty domains by default. Pass `?force=1` to
cascade-delete all tenant rows (Schedule, Playlist, Media, ApiToken,
Display, DisplayGroup, UserDomainRole) along with the domain itself.
The cascade is performed explicitly in Python rather than relying on
`ondelete='CASCADE'` because SQLite does not enforce FK cascades unless
`PRAGMA foreign_keys=ON` is set.

Domain admins (anyone holding the `domain.admin` permission within a
domain) can edit their own domain's name/description/quota/timezone/
brand color but cannot change the slug or `is_active` flag.

Slugs are validated against `^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$` and are
auto-lowercased on input. Both `name` and `slug` must be unique across
all domains.

---

## Role assignment

Each user has zero or more `UserDomainRole` rows linking
`(user_id, domain_id, role_id)`. Manage them through the **Roles**
button on the Users page (sidebar -> Admin -> User Management) or
through these endpoints:

| Endpoint                                              | Auth                              |
|-------------------------------------------------------|-----------------------------------|
| `GET /api/users/<id>/roles`                           | superadmin OR `domain.admin`      |
| `POST /api/users/<id>/roles`                          | scope: `domain.admin` of target domain |
| `DELETE /api/users/<id>/roles/<assignment_id>`        | scope: `domain.admin` of target domain |
| `GET /api/roles?domain_id=N`                          | scope: `domain.admin` of N        |

Behavior:
- **Roles assignable in domain N** = system roles (`domain_admin`,
  `content_editor`, `display_operator`, `viewer`) plus any custom roles
  scoped to that exact domain. A custom role from domain X cannot be
  assigned inside domain Y.
- **Domain admins** can manage memberships within their own domain only;
  they can't grant a user access to another tenant.
- **Self-lockout guard**: a non-superadmin cannot remove their own last
  `domain.admin` role inside the current domain — superadmins have other
  paths back in and are exempt.
- **`is_superadmin`** is a User-table flag, not a role; manage it through
  the user edit form, not here.

Audit actions: `user.role_assign`, `user.role_revoke`.

---

## Custom roles

System roles (`domain_id IS NULL`, `is_system=True`) are seeded by
bootstrap and immutable. Admins can build **custom roles** scoped to a
single domain via the Custom Roles page (sidebar -> Admin -> Custom
Roles, superadmin only) or through the API:

| Endpoint                      | Auth                          |
|-------------------------------|-------------------------------|
| `GET /api/permissions`        | any logged-in user (catalog read-only) |
| `GET /api/roles/<id>`         | system role: any user; custom role: `domain.admin` of role's domain |
| `POST /api/roles`             | `domain.admin` of target domain |
| `PUT /api/roles/<id>`         | `domain.admin` of role's domain |
| `DELETE /api/roles/<id>`      | `domain.admin` of role's domain |

Behavior:
- **Custom role names** must be unique within the domain *and* must not
  collide with any system role name (e.g. you can't create a custom
  `viewer`). The role picker would otherwise show ambiguous entries.
- **Permissions are replaced wholesale** on PUT (not patched). Sending
  `permissions: []` strips all permissions; omitting the key leaves them
  untouched.
- **Unknown permission keys cause a 400** listing the bad keys, rather
  than silently dropping them.
- **DELETE is blocked while assignments exist** (409 with
  `assignment_count`). Remove the assignments first; otherwise the
  delete would silently strip permissions from real users.
- **System roles are read-only**: PUT/DELETE on `is_system=True` returns
  400 (the issue is the target, not the requester, so 400 not 403).
- **A role's domain cannot be changed** after creation. The modal
  disables the domain dropdown in edit mode.

Audit actions: `role.create`, `role.update` (with from/to diffs),
`role.delete` (with full snapshot).

---

## Per-tenant branding

Each `Domain` row carries two branding fields:

| Field                     | Type   | Purpose                              |
|---------------------------|--------|--------------------------------------|
| `branding_primary_color`  | string | `#RRGGBB`. Drives the `--bs-primary` CSS variable. Falls back to `#0d6efd`. |
| `branding_logo_path`      | string | Storage rel-path (`d<N>/images/<uuid>.png`). Falls back to the bundled AISignX logo. |

`base.html` calls the `branding()` template helper (registered in
`app.py`'s context processor) on every render. The helper resolves the
active tenant's logo path through `storage.signed_url()` so the navbar
`<img>` works for both authenticated session users and the (theoretical)
unauthenticated logo-fetch case.

Endpoints:

| Endpoint                                          | Auth                              |
|---------------------------------------------------|-----------------------------------|
| `POST /api/domains/<id>/branding/logo`            | superadmin OR `domain.admin`      |
| `DELETE /api/domains/<id>/branding/logo`          | superadmin OR `domain.admin`      |
| `PUT /api/domains/<id>` (with `branding_primary_color`) | superadmin OR `domain.admin` |

The logo upload accepts PNG, JPG, GIF, WebP, or SVG up to 2MB. Old
files are deleted from disk on replace/clear (best effort -- a Windows
file-handle race may leave stale bytes that the next periodic cleanup
sweeps).

Audit action: `domain.branding_update`.

---

## Display group hierarchy

`DisplayGroup` rows form a forest within each tenant. The optional
`parent_id` column self-references another group in the **same**
domain. `ondelete=SET NULL` means deleting a parent reparents its
children to the root rather than cascading them away.

Endpoints:

| Endpoint                                            | Behavior                                  |
|-----------------------------------------------------|-------------------------------------------|
| `GET /api/groups`                                   | Flat list (back-compat)                   |
| `GET /api/groups?tree=true`                         | Nested forest, sorted by name at every level |
| `POST /api/groups` body: `{name, description, parent_id?}` | Create; validates parent in same tenant |
| `PUT /api/groups/<id>` body may include `parent_id` | Reparent. Refuses to create a cycle (400) |
| `GET /api/groups/<id>/displays`                     | Direct displays only (back-compat)        |
| `GET /api/groups/<id>/displays?recursive=true`      | Direct + descendants -- the **effective** members |

Validation rules enforced by the API:

- A group cannot be its own parent.
- A group cannot be a descendant of its proposed parent (cycle prevention).
- The proposed parent must belong to the same tenant. The tenant filter
  hides cross-tenant rows from `Group.query.get()` so cross-tenant parents
  appear as "not found" (400).
- The UI excludes the group itself **and all its descendants** from the
  parent dropdown so users can't construct a cycle through the form.

The recursive displays endpoint is the foundation for future schedule
and playlist propagation: assigning content to a parent group will be
the natural way to deliver it to every display in every descendant
group too.

Audit:
- `group.create` payload includes `parent_id`
- `group.update` diffs include `parent_id` when changed
- `group.delete` payload includes the deleted parent_id and the children
  reparented to root (`reparented_child_ids`)

### Schema migration

`db.create_all()` only creates new tables, never alters existing ones.
The `parent_id` column is added at boot by `bootstrap._evolve_schema()`,
which inspects the live schema and runs `ALTER TABLE display_group ADD
COLUMN parent_id ... ON DELETE SET NULL` only if the column is missing.
This is idempotent and safe to re-run on every start. Failures are
logged but never raised -- a botched ALTER on a running server is worse
than a missing column we'll re-attempt next start.

---

## Plugin sandboxing & per-tenant policy

Plugins run inside <iframe> elements served from /plugin/<key>.
Phase 3 introduces three layered controls:

1. **Plugin permission manifest** -- each plugin's plugin.json may
   declare a `permissions` list. Unknown entries are dropped after a
   warning so a typo in the manifest never breaks the plugin entirely.

2. **Per-tenant policy** -- `DomainPluginPolicy` is a tenant-scoped
   table keyed on (`domain_id`, `plugin_key`). It carries an
   `enabled` flag and an optional `granted_permissions` list. Absence
   of a row means "default policy": enabled, with all declared
   permissions granted. Existing installs work unchanged.

3. **Iframe sandbox derivation** -- `compute_sandbox_attrs(granted)`
   maps the granted permission list to the iframe `sandbox=` and
   `allow=` strings. The display player applies them when creating
   each plugin iframe, so each plugin runs with the minimum
   capabilities -- never the full legacy permissive set.

### Permission catalog

Defined in `plugin_system.PLUGIN_PERMISSION_CATALOG`. Each entry maps
to iframe sandbox tokens or Permissions-Policy `allow=` features:

| Permission        | Sandbox token(s)                                | Allow feature       |
|-------------------|--------------------------------------------------|---------------------|
| `forms.submit`  | `allow-forms`                                  |                     |
| `popups`        | `allow-popups` `allow-popups-to-escape-sandbox` |                  |
| `modals`        | `allow-modals`                                 |                     |
| `pointer.lock`  | `allow-pointer-lock`                           |                     |
| `orientation.lock` | `allow-orientation-lock`                    |                     |
| `top.navigation` | `allow-top-navigation`                        |                     |
| `presentation`  | `allow-presentation`                           |                     |
| `fullscreen`    |                                                  | `fullscreen`      |
| `autoplay`      |                                                  | `autoplay`        |
| `camera`        |                                                  | `camera`          |
| `microphone`    |                                                  | `microphone`      |
| `geolocation`   |                                                  | `geolocation`     |
| `clipboard.read` |                                                 | `clipboard-read`  |
| `clipboard.write` |                                                | `clipboard-write` |
| `network.fetch` | (advisory only)                                  |                     |
| `storage.local` | (advisory only)                                  |                     |

Baseline tokens always present: `allow-scripts allow-same-origin`.
Without these the plugin's main.js cannot run and `url_for()` URLs
break.

### Endpoints

| Endpoint                                        | Auth                                |
|-------------------------------------------------|-------------------------------------|
| `GET /admin/plugin-policy`                    | superadmin                          |
| `GET /api/plugin-policy?domain_id=N`          | `domain.admin` of N (or superadmin) |
| `PUT /api/plugin-policy/<key>?domain_id=N`    | `domain.admin` of N (or superadmin) |

### Authoring a sandboxed plugin

In `plugin.json`:

`\\\json
{
  "key": "my_plugin",
  "name": "My Plugin",
  "version": "1.0.0",
  "permissions": ["network.fetch", "storage.local"],
  "schema": [...]
}
\\\`

In `main.js`, plugins can read `window.PLUGIN_PERMISSIONS` to
degrade gracefully. The list is the **intersection** of the plugin's
declared permissions and the tenant's grant, so a plugin can detect
e.g. that `network.fetch` was withheld and fall back to bundled
sample data.

### Failure modes

- Plugin disabled in tenant -> `/plugin/<key>` returns 403; playlist
  builder still emits the item but `plugin.enabled=false` flags it
  for client-side handling.
- Policy lookup fails (DB error) -> falls back to `enabled=True` with
  declared permissions. Locking everyone out of plugins because of a
  transient DB hiccup is worse than the alternative.
- Granting a permission the plugin never declared -> silently dropped
  on save (the iframe would carry capabilities the plugin doesn't even
  know exist; nothing good comes of that).
- Granting a permission unknown to the catalog -> `400` with the
  bad keys listed.

Audit action: `plugin_policy.update` with from/to diff.

---

## Schedule inheritance through group hierarchy

Building on the display group hierarchy work: a schedule attached to a
**parent group** automatically applies to every display in every
descendant group. The inheritance walk is **upward only** -- a display
sees its own group's schedules and every ancestor's schedules, never
sibling or descendant groups.

### How it works

`groups.resolve_effective_group_ids(display)` returns
`[display.group_id, *_ancestor_ids(display.group_id)]`. The schedule
resolvers in `playlists.py` and `display_player.py` pass that list
into `Schedule.group_id.in_(...)` instead of the previous
`Schedule.group_id == display.group_id` -- a single-line conceptual
change, three substitution sites.

### Resolution order

Within the candidate set (display-direct + all ancestor-group
schedules), ordering still falls back to the existing `priority DESC,
id ASC` tiebreak. Direct display schedules and group schedules sit in
the same priority pool -- there's no "display wins over group" rule
beyond what `priority` expresses. Operators who want a hierarchy of
overrides can encode it by setting higher `priority` on more-specific
schedules.

### Sibling isolation

Inheritance is **strictly upward**. A display in group A does NOT see
schedules attached to group B even if A and B share a parent. This
keeps tenant operators from accidentally broadcasting content across
the wrong subtree.

### Operational use

- Attach a single corporate-branding schedule to the root group.
  Every display in the tenant inherits it automatically.
- Override it for the lobby subtree by attaching a higher-priority
  schedule to the lobby parent group.
- Override that further on a single display by setting
  `Schedule.display_id` and a yet-higher priority.

### Performance

The `_ancestor_ids` walk is `O(depth)` -- typically depth <= 4 in
real installs. The candidate set is bounded by the tenant's schedule
count which is small. No closure tables or recursive CTEs needed at
this scale.

---

## Synchronized playback

Toggling `sync_playback` on a `DisplayGroup` makes every display in
that group show the same slide at the same moment without continuous
server coordination. The mechanism is a wall-clock anchor + a
deterministic per-item duration table -- once the displays know both,
they stay in lockstep on their own.

### Wire format

When `sync_playback` is on, the playlist response gains a `sync`
block:

`\\\json
{
  "playlist": {
    "items": [...],
    "version": "abc123...",
    "sync": {
      "enabled":           true,
      "group_id":          7,
      "anchor_unix_ms":    1714589000000,
      "cycle_total_ms":    90000,
      "item_durations_ms": [30000, 30000, 30000],
      "server_now_ms":     1714589123456
    }
  }
}
\\\`

The display computes:

`\\\
elapsed = (now_ms - anchor_unix_ms) % cycle_total_ms
\\\`

then walks `item_durations_ms` to find the slot containing
`elapsed`. That slot's index is the slide it should be showing right
now; the remainder of the slot is how long until it should advance.

### Clock skew

Each display calls `GET /api/display/<token>/server_time` once on
startup to estimate the server-vs-local clock offset. The estimate is
refined every time a new playlist payload arrives (the embedded
`server_now_ms` is a free measurement). Sub-second sync is achievable
on a LAN; even a 200ms network hiccup leaves displays aligned to within
a slide rather than dropping out of sync.

### Anchor stability

Anchors are cached in `system_settings` under
`sync_anchor.<group_id>.<playlist_version>`. Same group + same
playlist version => same anchor across server restarts and SSE
reconnects. A playlist edit that changes the version produces a new
anchor; old anchor keys quietly age out of relevance.

### Early-advance signals are ignored

Plugin `signage:complete` postMessages and video `ended` events both
**advance the playlist immediately** in normal (unsynced) playback. In
synced playback they are ignored -- the wall-clock timer is the only
authority. Operators of synced groups should set explicit, accurate
`duration` values on every playlist item and avoid plugins whose
content runs longer than the configured slot.

### Joining mid-cycle

A display joining a synced group mid-cycle jumps straight to the
correct slide rather than starting at slide 0. The same logic recovers
displays that drifted (e.g. a video clip ran 200ms past its slot) -- on
the next `advance()` they re-align to the wall-clock target instead
of incrementing the index.

### Tenant scope

The anchor key is **not** tenant-namespaced because `group_id` is
already globally unique. Cross-tenant access is impossible: a display
only ever sees its own group's anchor (the rest of the response is
gated by `api_key` -> `Display` lookup, which carries the tenant).

---

## Per-display capability negotiation

Each display self-reports what it can render; the server uses that
report to filter playlist items the display would just choke on
(wrong codec, oversized image, etc).

### Endpoint

`POST /api/display/<token>/capabilities`

Body (all fields optional):

```json
{
  "max_video_height":  1080,
  "max_image_dim":     4096,
  "screen_w":          1920,
  "screen_h":          1080,
  "codecs":            ["h264", "vp9"],
  "audio":             true,
  "browser":           "Chrome/126"
}
```

Idempotent. The display calls this on first connect and again after any
environment change (resolution swap, codec install). The server audits
`display.capabilities_update` only when the snapshot actually changed
-- repeated identical reports return `changed=false` and skip the
audit, so heartbeat-style polling doesn't flood the log.

### Filtering rules

* Image / webpage / plugin items: always allowed.
* Video items: filtered when both the media's codec is known **and**
  the display's `codecs` list is known **and** the codec is missing.
  Both unknowns => "compatible" (see "Why unknown means allowed" below).
* Plugin items: per-tenant policy still applies (`DomainPluginPolicy`)
  but no extra capability gating happens here today; reserved for
  future plugin permission/capability cross-checks.

### Why "unknown means allowed"

Capability reports are best-effort. A first-boot client may not have
sent its capabilities yet; a legacy client may never send them. Making
"unknown" mean "denied" would cause every display in the field to go
blank on the first connect after this code ships -- catastrophic for
an operator-visible feature. The trade-off: a display that never
reports caps may silently fail on incompatible content. Operators can
spot that on the display detail page -- the "Reported Capabilities"
card shows a placeholder when no report has arrived.

### Codec inference

The schema doesn't store an explicit codec field on `Media`; today
codec is inferred from the file extension (`.mp4` -> h264,
`.webm` -> vp9, etc). When the variant pipeline starts annotating
files with codec metadata, `capabilities._video_codec_hint()` will
prefer the explicit value over the extension-based guess. Both paths
are already wired -- adding the field is a one-line change.

### Operator-facing surfaces

* Display detail page (`/display/<id>`) shows the "Reported
  Capabilities" card with screen size, max video / image dimensions,
  declared codecs, and the UA hint. Empty state explains what to
  expect.
* Playlist responses include `excluded_items` so a debug client can
  see which items were dropped and why. Each entry has `item_id`,
  `media_id`, `name`, and a short `reason` string.
* Audit action `display.capabilities_update` carries a from/to diff
  of the capability dict.

---

## Plugin CSP origin pinning (Phase 4)

A plugin manifest may declare `"csp_origins": ["https://api.example.com"]` to whitelist the outbound origins it is allowed to talk to. The runner page (`/plugin/<type>`) emits a `Content-Security-Policy` header that locks the iframe down to those origins (plus `'self'`). Plugins with no `csp_origins` get the strict default (`'self'` only).

Manifest example (`plugins/weather/plugin.json`):

```json
{
  "key": "weather",
  "permissions": ["network.fetch", "storage.local"],
  "csp_origins": [
    "https://api.open-meteo.com",
    "https://geocoding-api.open-meteo.com"
  ]
}
```

Validation: each origin must match `^(https?|wss?)://(\\*\\.)?host[:port]\$`. Bad entries are dropped with a warning at registry-load time. The validated list is exposed to the plugin via `window.PLUGIN_CSP_ORIGINS` for graceful degradation.

---

## Plugin policy push notifications (Phase 4)

When a domain's `DomainPluginPolicy` is updated via `PUT /api/plugin-policy/<plugin_key>`, the server immediately pushes a `plugin_policy` SSE event to every connected display in that domain. The display player (`static/js/display_player.js`) forwards the change to the matching plugin iframes via `postMessage` and reloads each one so the new sandbox + CSP take effect.

Client-side hook for plugins:

```javascript
window.addEventListener('signage:plugin_policy_changed', (e) => {
  console.log('policy changed:', e.detail);
  // e.detail = { plugin_key, enabled, granted_permissions }
});
```

Iframe tagging: every plugin iframe carries `data-plugin-key` and `data-plugin-type` so the broadcast can find and reload the right frame without affecting non-plugin webpages.

---

## Plugin signing & registry verification (Phase 4)

Goal: detect plugin tampering and (optionally) refuse to load plugins whose code has changed since they were last signed.

Algorithm: HMAC-SHA256 over (folder name + sorted file SHA-256 list + canonical JSON of plugin.json with `signature` / `signed_files_hash` removed). Suitable for single-organization deployments; a peer server can be trusted by adding its hex secret to `plugin.signing.trust_list`.

Settings:

| Key | Default | Meaning |
|-----|---------|---------|
| `plugin.signing.secret` | auto | Hex HMAC secret. Auto-generated on first boot; sensitive. |
| `plugin.signing.trust_list` | `[]` | Extra hex secrets accepted during verification. The local secret is always trusted. |
| `plugin.signing.require_signed` | `false` | When true, the runner refuses unsigned/invalid plugins. |

Admin endpoints (superadmin):

```text
GET  /api/plugin-signing/status            -> { plugins: [{plugin_key, signature_status, ...}], require_signed, trusted_secret_count }
POST /api/plugin-signing/sign/<plugin_key> -> sign one plugin in place
POST /api/plugin-signing/sign-all          -> sign every registered plugin
POST /api/plugin-signing/rotate-secret     -> generate a new local secret (existing signatures need re-signing)
```

Each plugin's registry meta carries `signature_status` (`valid` / `invalid` / `unsigned` / `missing_secret`) and a human-readable `signature_detail`. Audit actions: `plugin.sign`, `plugin.sign_all`, `plugin.signing.rotate_secret`.

