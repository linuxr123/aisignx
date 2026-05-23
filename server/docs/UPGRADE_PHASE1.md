# Phase 1 Upgrade Runbook

If you have an existing single-tenant AISignX install and want to move to
Phase 1 (multi-tenancy + RBAC + storage layer + jobs), follow this runbook.
**A fresh install does NOT need this** — `bootstrap.py` handles everything
on first boot.

---

## Before you start

1. **Back up your database.** This is a one-way migration; rollback requires
   restoring the backup.
2. **Back up `uploads/`.** The migration moves files into per-tenant
   subdirectories.
3. **Schedule downtime.** The app must be stopped during steps 2 and 3.

---

## Step 1: Update the code

```bash
git pull
pip install -r requirements.txt
```

`requirements.txt` is unchanged in Phase 1; this is just a sanity step.

---

## Step 2: Run the schema migration

```bash
# Stop the app
systemctl stop aisignx       # or however you run it

# Apply the Phase 1 migration
flask db upgrade
```

The migration adds these tables:
- `domain`, `permission`, `role`, `role_permission`, `user_domain_role`
- `audit_log`, `system_setting`

And adds these columns to existing tables:
- `user.is_superadmin` (renamed from `is_admin`; old column dropped)
- `display.domain_id`, `display_group.domain_id`, `media.domain_id`,
  `playlist.domain_id`, `schedule.domain_id`, `api_token.domain_id`,
  `emergency_broadcast.domain_id`, `emergency_template.domain_id`
- `media.checksum_sha256`, `media.duration_seconds`, `media.codec`,
  `media.bitrate_bps` (informational, populated by Phase 2 transcode jobs)

**The migration leaves all `domain_id` columns NULL.** The next step
backfills them.

---

## Step 3: Run the data backfill

```bash
python -m tools.upgrade_phase1_backfill
```

This script:
1. Creates the `Default` domain if no domains exist
2. Promotes the existing admin user to `is_superadmin = True` and assigns
   them the `domain_admin` role in the Default domain
3. Stamps `domain_id = <Default.id>` on every existing display, media,
   playlist, schedule, group, token, and broadcast
4. Moves files from `uploads/images/`, `uploads/videos/`, `uploads/thumbnails/`
   into `uploads/d<id>/images/` etc., and rewrites `Media.file_path` /
   `Media.thumbnail_path` to the new layout
5. Recomputes `Domain.storage_used_bytes` from the actual file sizes
6. Seeds permissions, roles, and `auto.*` settings

The script is idempotent — safe to re-run if it crashes partway.

> **Note:** This backfill script is NOT yet bundled. For Phase 1 development
> the recommended path is a fresh install. If you need to migrate an
> existing install, file an issue and we'll prioritize the script.

---

## Step 4: Verify

```bash
# Start the app
systemctl start aisignx

# Smoke test
curl -fsS http://localhost:5000/login > /dev/null && echo OK

# Check tenant scoping
python -m tools.audit_tenant_leaks
```

The leak scanner should report `0 finding(s)`.

Log in with your existing admin credentials. You should see:
- The `Default` tenant in the tenant switcher (if you have multiple tenants;
  hidden if only one)
- All your existing displays, media, playlists, etc.
- A new `Audit Log` page (visible to anyone with `audit.read`)

---

## Step 5 (optional): Create additional tenants

```python
# In a Python shell
from app import app
from bootstrap import _seed_default_domain
from models import Domain, db
from tenant_filter import bypass_tenant_filter

with app.app_context():
	with bypass_tenant_filter():
		d = Domain(name='Acme Corp', slug='acme', features={})
		db.session.add(d); db.session.commit()
```

The tenant management UI is now available to superusers from the admin
navigation. Internal table/model names still use `Domain`.

---

## Troubleshooting

### "Refusing to insert ... with no domain_id and no tenant context"

You're running code outside a request that tries to insert into a tenant
table. Either wrap in `bypass_tenant_filter()` and set `domain_id`
explicitly, or call `tenant_filter.set_current_domain_id(N)` first.

### "Cross-tenant insert blocked"

A route is trying to insert a row with a `domain_id` that doesn't match the
request's active tenant. This is the safety net working as intended. Find
the offending insert and remove the explicit `domain_id=...` (auto-stamp
will use the correct value).

### "Storage quota exceeded for domain ..."

Set `Domain.storage_quota_bytes = NULL` to lift the limit, or raise it.
Default is `NULL` (unlimited) on the seeded Default domain.

### Old uploads/ files don't appear in the UI

The backfill rewrites `file_path` to the new layout. If you see a 404 in
the player, run:

```python
with app.app_context():
	from storage import recompute_used
	for d in Domain.query.all():
		print(d.slug, recompute_used(d.id))
```

If the size is 0 but the files exist on disk, the `file_path` column wasn't
updated — re-run the backfill.

---

## Rolling back

There is no automated rollback. Restore your DB backup and `uploads/`
directory, then `git checkout <pre-phase-1-commit>`.
