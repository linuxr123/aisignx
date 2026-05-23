"""
First-boot bootstrap - Phase 1, Task 8.

Idempotent. Run on every server start. Detects "fresh install" by checking
whether the Domain table is empty; if so, seeds:

    1. system_settings auto.* keys (from system_caps)
    2. system_settings.security.signing_key (random 32 bytes hex)
    3. system_settings.default_timezone (from tzlocal if available, else UTC)
    4. all Permission rows from permissions.PERMISSIONS
    5. all system Role rows from permissions.SYSTEM_ROLES
    6. a Default Domain
    7. assigns the first existing User (the admin/Admin123! created by
       app.init_db()) the domain_admin role within the Default domain.
       The user keeps is_superadmin=True so they can manage other domains.

If permissions or roles already exist, only missing rows are added (so new
Phase-2+ permissions can be backfilled here without losing customizations).
"""
import secrets

from models import (db, Domain, Permission, Role, role_permission,
                    UserDomainRole, User, SystemSetting)
from tenant_filter import bypass_tenant_filter
import settings as settings_mod
import system_caps
from permissions import PERMISSIONS, SYSTEM_ROLES
from logging_config import logger


DEFAULT_DOMAIN_SLUG = 'default'
DEFAULT_DOMAIN_NAME = 'Default'


def default_tenant_domain_id():
    """ID of the seeded default tenant for superuser session context."""
    with bypass_tenant_filter():
        d = (Domain.query
             .filter_by(slug=DEFAULT_DOMAIN_SLUG, is_active=True)
             .first())
        if d is not None:
            return d.id
        d = (Domain.query.filter_by(is_active=True)
             .order_by(Domain.id.asc())
             .first())
        return d.id if d is not None else None


def _seed_settings(app):
    """Write all auto.* keys + signing key + default tz, idempotent."""
    uploads = app.config.get('UPLOAD_FOLDER', '.')
    auto = system_caps.all_auto_settings(uploads_path=uploads)
    for k, v in auto.items():
        # auto.* keys live outside BUILTIN_DEFAULTS; pass _allow_unknown.
        vtype = ('int' if isinstance(v, bool) is False and isinstance(v, int)
                 else 'bool' if isinstance(v, bool)
                 else 'string')
        settings_mod.set(k, v, is_auto=True, value_type=vtype,
                         _allow_unknown=True)

    # Signing key: only generate once.
    if not settings_mod.get('security.signing_key'):
        settings_mod.set('security.signing_key',
                         secrets.token_hex(32))
        logger.info('Generated security.signing_key (32 bytes).')

    # Default timezone: only set if absent.
    if not settings_mod.get('default_timezone'):
        tz = 'UTC'
        try:
            import tzlocal
            tz = str(tzlocal.get_localzone())
        except Exception:
            pass
        settings_mod.set('default_timezone', tz)
        logger.info(f'Set default_timezone = {tz}')


def _seed_permissions():
    """Insert any missing Permission rows. Existing rows are left alone."""
    existing = {p.key for p in Permission.query.all()}
    added = 0
    for key, desc in PERMISSIONS.items():
        if key in existing:
            continue
        db.session.add(Permission(key=key, description=desc, is_system=True))
        added += 1
    if added:
        db.session.commit()
        logger.info(f'Seeded {added} permission rows.')


def _seed_system_roles():
    """Create the four system roles (domain_id NULL, is_system True) and
    sync their permission lists. Idempotent: existing roles get their
    permissions re-applied (so Phase-2+ additions land on system roles)."""
    perm_by_key = {p.key: p for p in Permission.query.all()}
    for role_name, spec in SYSTEM_ROLES.items():
        role = (Role.query
                .filter_by(domain_id=None, name=role_name, is_system=True)
                .one_or_none())
        if role is None:
            role = Role(domain_id=None, name=role_name, is_system=True,
                        description=spec['description'])
            db.session.add(role)
            db.session.flush()
            logger.info(f'Created system role: {role_name}')

        wanted = {perm_by_key[k] for k in spec['permissions'] if k in perm_by_key}
        current = set(role.permissions)
        to_add = wanted - current
        if to_add:
            for p in to_add:
                role.permissions.append(p)
            logger.info(f'Role {role_name}: added {len(to_add)} permissions.')
    db.session.commit()


def _seed_default_domain():
    """Create the Default domain if no domains exist. Returns the Domain row."""
    with bypass_tenant_filter():
        existing = Domain.query.first()
        if existing is not None:
            return existing
        d = Domain(name=DEFAULT_DOMAIN_NAME, slug=DEFAULT_DOMAIN_SLUG,
                   description='Default tenant created on first install. '
                               'Rename or add additional domains as needed.',
                   features={}, is_active=True)
        db.session.add(d)
        db.session.commit()
        logger.info(f'Created default domain: {DEFAULT_DOMAIN_NAME!r} '
                    f'(slug={DEFAULT_DOMAIN_SLUG})')
        return d


def _assign_initial_admin(domain):
    """If a User exists but has no domain assignment, give them domain_admin
    in the default domain. Bootstraps the admin/Admin123! user created by
    app.init_db()."""
    with bypass_tenant_filter():
        user = User.query.order_by(User.id.asc()).first()
        if user is None:
            return
        already = UserDomainRole.query.filter_by(user_id=user.id,
                                                 domain_id=domain.id).first()
        if already is not None:
            return
        role = (Role.query
                .filter_by(domain_id=None, name='domain_admin', is_system=True)
                .one_or_none())
        if role is None:
            return
        db.session.add(UserDomainRole(user_id=user.id, domain_id=domain.id,
                                      role_id=role.id))
        db.session.commit()
        logger.info(f'Assigned user {user.username!r} the domain_admin role '
                    f'in domain {domain.slug!r}.')


def run(app):
    """Run all bootstrap steps inside an app context. Safe to call repeatedly."""
    with app.app_context():
        _evolve_schema()
        _seed_settings(app)
        _seed_permissions()
        _seed_system_roles()
        domain = _seed_default_domain()
        _assign_initial_admin(domain)


def _evolve_schema():
    """Apply lightweight, idempotent schema additions that `db.create_all()`
    cannot make (it only creates new tables, never alters existing ones).

    This is the safety net for installs that don't have alembic in their
    deploy pipeline. Each block:
      1. Inspects the live schema.
      2. Adds the column if missing.
    Failures are logged but never raise -- a botched ALTER on a running
    server is worse than a missing column we'll re-attempt next start.
    """
    from sqlalchemy import inspect, text
    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('display_group')}
        if 'parent_id' not in existing:
            with db.engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE display_group ADD COLUMN parent_id INTEGER '
                    'REFERENCES display_group(id) ON DELETE SET NULL'))
                conn.execute(text(
                    'CREATE INDEX IF NOT EXISTS ix_display_group_parent_id '
                    'ON display_group(parent_id)'))
            logger.info('schema: added display_group.parent_id')
    except Exception as e:
        logger.warning(f'schema: display_group.parent_id evolve failed: {e}')

    # display.app_version: client-reported version string. Added so the
    # displays-page Version column and the bulk-update poller can surface
    # rollouts in real time. Safe to add on the fly; defaults to NULL.
    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('display')}
        with db.engine.begin() as conn:
            if 'app_version' not in existing:
                conn.execute(text(
                    'ALTER TABLE display ADD COLUMN app_version VARCHAR(40)'))
                logger.info('schema: added display.app_version')
            # display.volume: master playback volume 0-100. Default 100
            # (full volume) so existing rows behave sensibly. The browser
            # player applies this value to <video> elements, with 0 also
            # forcing the muted attribute so autoplay still works on
            # browsers that block audio playback at full volume.
            if 'volume' not in existing:
                conn.execute(text(
                    'ALTER TABLE display ADD COLUMN volume INTEGER '
                    'NOT NULL DEFAULT 100'))
                logger.info('schema: added display.volume')
            if 'auto_update_client' not in existing:
                conn.execute(text(
                    'ALTER TABLE display ADD COLUMN auto_update_client '
                    'BOOLEAN NOT NULL DEFAULT 0'))
                logger.info('schema: added display.auto_update_client')
            if 'sync_playback_opt_out' not in existing:
                conn.execute(text(
                    'ALTER TABLE display ADD COLUMN sync_playback_opt_out '
                    'BOOLEAN NOT NULL DEFAULT 0'))
                logger.info('schema: added display.sync_playback_opt_out')
            if 'diagnostics_enabled' not in existing:
                conn.execute(text(
                    'ALTER TABLE display ADD COLUMN diagnostics_enabled '
                    'BOOLEAN NOT NULL DEFAULT 0'))
                logger.info('schema: added display.diagnostics_enabled')
    except Exception as e:
        logger.warning(f'schema: display.app_version evolve failed: {e}')

    # Media: thumbnail status + last-screenshot timestamp. Both default to
    # safe values so existing rows behave as before; the media UI uses
    # them for the new "thumbnail status" badge and webpage refresh job.
    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('media')}
        with db.engine.begin() as conn:
            if 'thumbnail_status' not in existing:
                conn.execute(text(
                    "ALTER TABLE media ADD COLUMN thumbnail_status VARCHAR(20) "
                    "DEFAULT 'ok'"))
                logger.info('schema: added media.thumbnail_status')
            if 'thumbnail_generated_at' not in existing:
                conn.execute(text(
                    'ALTER TABLE media ADD COLUMN thumbnail_generated_at DATETIME'))
                logger.info('schema: added media.thumbnail_generated_at')
            if 'tags' not in existing:
                conn.execute(text(
                    "ALTER TABLE media ADD COLUMN tags VARCHAR(500) DEFAULT ''"))
                logger.info('schema: added media.tags')
            if 'folder' not in existing:
                conn.execute(text(
                    "ALTER TABLE media ADD COLUMN folder VARCHAR(255) DEFAULT ''"))
                # Best-effort index; ignore if the dialect already has it
                # or if a concurrent bootstrap created it first.
                try:
                    conn.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_media_folder ON media (folder)'))
                except Exception:
                    pass
                logger.info('schema: added media.folder')
            # media.audio_enabled: per-file default for whether a video's
            # audio plays. Default 1 (audio on) so existing video rows
            # keep emitting sound when the new playlist/display volume
            # controls land. Only meaningful for video media; ignored
            # for images/webpages/plugins.
            if 'audio_enabled' not in existing:
                conn.execute(text(
                    'ALTER TABLE media ADD COLUMN audio_enabled '
                    'BOOLEAN NOT NULL DEFAULT 1'))
                logger.info('schema: added media.audio_enabled')
    except Exception as e:
        logger.warning(f'schema: media thumbnail columns evolve failed: {e}')

    # PlaylistItem.transition / aspect_mode / mute_audio: per-item playback
    # tweaks. The model already declares these, but older databases never
    # had them. Add idempotently so saved playlists keep working and the
    # editor can edit them.
    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('playlist_item')}
        with db.engine.begin() as conn:
            if 'transition' not in existing:
                conn.execute(text(
                    "ALTER TABLE playlist_item ADD COLUMN transition "
                    "VARCHAR(20) NOT NULL DEFAULT 'cut'"))
                logger.info('schema: added playlist_item.transition')
            if 'aspect_mode' not in existing:
                conn.execute(text(
                    'ALTER TABLE playlist_item ADD COLUMN aspect_mode VARCHAR(20)'))
                logger.info('schema: added playlist_item.aspect_mode')
            if 'mute_audio' not in existing:
                conn.execute(text(
                    'ALTER TABLE playlist_item ADD COLUMN mute_audio '
                    'BOOLEAN NOT NULL DEFAULT 0'))
                logger.info('schema: added playlist_item.mute_audio')
    except Exception as e:
        logger.warning(f'schema: playlist_item columns evolve failed: {e}')

    # Playlist-level default transition (applied to items that don't override
    # their own). Older databases never had this column.
    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('playlist')}
        with db.engine.begin() as conn:
            if 'default_transition' not in existing:
                conn.execute(text(
                    'ALTER TABLE playlist ADD COLUMN default_transition VARCHAR(16)'))
                logger.info('schema: added playlist.default_transition')
            if 'smart_rules' not in existing:
                conn.execute(text(
                    'ALTER TABLE playlist ADD COLUMN smart_rules TEXT'))
                logger.info('schema: added playlist.smart_rules')
            if 'smart_order' not in existing:
                conn.execute(text(
                    "ALTER TABLE playlist ADD COLUMN smart_order VARCHAR(32)"))
                logger.info('schema: added playlist.smart_order')
            if 'smart_limit' not in existing:
                conn.execute(text(
                    'ALTER TABLE playlist ADD COLUMN smart_limit INTEGER'))
                logger.info('schema: added playlist.smart_limit')
            # playlist.video_audio_default: 'inherit' (use the per-media
            # default), 'on' (force audio on for every video in this
            # playlist), or 'off' (force audio off for every video). Wins
            # against the media-level default but loses to the per-item
            # mute_audio override. Default 'inherit'.
            if 'video_audio_default' not in existing:
                conn.execute(text(
                    "ALTER TABLE playlist ADD COLUMN video_audio_default "
                    "VARCHAR(10) NOT NULL DEFAULT 'inherit'"))
                logger.info('schema: added playlist.video_audio_default')
            # Comma-separated whitelist of transition names the player may
            # pick from when default_transition='random'. Empty / NULL
            # means "use the built-in default pool". See playlists.py.
            if 'random_transitions' not in existing:
                conn.execute(text(
                    "ALTER TABLE playlist ADD COLUMN random_transitions "
                    "VARCHAR(255) DEFAULT ''"))
                logger.info('schema: added playlist.random_transitions')
    except Exception as e:
        logger.warning(f'schema: playlist columns evolve failed: {e}')

    # Domain enrollment code (proof-of-invitation for device registration)
    # and PendingDisplay tenancy/audit columns. Without these, /api/register
    # cannot validate tenant membership and bootstrap blows up on the
    # initial Domain.query because the ORM SELECTs the new columns.
    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('domain')}
        with db.engine.begin() as conn:
            if 'enrollment_code' not in existing:
                conn.execute(text(
                    'ALTER TABLE domain ADD COLUMN enrollment_code VARCHAR(40)'))
                try:
                    conn.execute(text(
                        'CREATE UNIQUE INDEX IF NOT EXISTS ix_domain_enrollment_code '
                        'ON domain (enrollment_code)'))
                except Exception:
                    pass
                logger.info('schema: added domain.enrollment_code')
            if 'enrollment_code_expires_at' not in existing:
                conn.execute(text(
                    'ALTER TABLE domain ADD COLUMN enrollment_code_expires_at DATETIME'))
                logger.info('schema: added domain.enrollment_code_expires_at')
            if 'enrollment_enabled' not in existing:
                conn.execute(text(
                    'ALTER TABLE domain ADD COLUMN enrollment_enabled '
                    'BOOLEAN NOT NULL DEFAULT 1'))
                logger.info('schema: added domain.enrollment_enabled')
            if 'storage_root_path' not in existing:
                conn.execute(text(
                    'ALTER TABLE domain ADD COLUMN storage_root_path VARCHAR(1024)'))
                logger.info('schema: added domain.storage_root_path')
    except Exception as e:
        logger.warning(f'schema: domain enrollment columns evolve failed: {e}')

    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('pending_display')}
        with db.engine.begin() as conn:
            if 'domain_id' not in existing:
                conn.execute(text(
                    'ALTER TABLE pending_display ADD COLUMN domain_id INTEGER '
                    'REFERENCES domain(id)'))
                try:
                    conn.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_pending_display_domain_id '
                        'ON pending_display (domain_id)'))
                except Exception:
                    pass
                logger.info('schema: added pending_display.domain_id')
            if 'approved_domain_id' not in existing:
                conn.execute(text(
                    'ALTER TABLE pending_display ADD COLUMN approved_domain_id '
                    'INTEGER REFERENCES domain(id)'))
                logger.info('schema: added pending_display.approved_domain_id')
            if 'enrollment_code_used' not in existing:
                conn.execute(text(
                    'ALTER TABLE pending_display ADD COLUMN enrollment_code_used '
                    'VARCHAR(40)'))
                try:
                    conn.execute(text(
                        'CREATE INDEX IF NOT EXISTS ix_pending_display_enrollment_code_used '
                        'ON pending_display (enrollment_code_used)'))
                except Exception:
                    pass
                logger.info('schema: added pending_display.enrollment_code_used')
            if 'user_agent' not in existing:
                conn.execute(text(
                    'ALTER TABLE pending_display ADD COLUMN user_agent VARCHAR(255)'))
                logger.info('schema: added pending_display.user_agent')
    except Exception as e:
        logger.warning(f'schema: pending_display columns evolve failed: {e}')

    try:
        insp = inspect(db.engine)
        existing = {c['name'] for c in insp.get_columns('user')}
        if 'is_service_account' not in existing:
            with db.engine.begin() as conn:
                conn.execute(text(
                    'ALTER TABLE user ADD COLUMN is_service_account '
                    'BOOLEAN NOT NULL DEFAULT 0'))
            logger.info('schema: added user.is_service_account')
    except Exception as e:
        logger.warning(f'schema: user.is_service_account evolve failed: {e}')

    try:
        _evolve_user_tenant_login()
    except Exception as e:
        logger.warning(f'schema: user tenant login evolve failed: {e}')


def _evolve_user_tenant_login():
    """Per-tenant usernames: home_domain_id + composite unique indexes."""
    from sqlalchemy import inspect, text
    from user_accounts import backfill_home_domain_from_roles

    insp = inspect(db.engine)
    existing = {c['name'] for c in insp.get_columns('user')}
    if 'home_domain_id' not in existing:
        with db.engine.begin() as conn:
            conn.execute(text(
                'ALTER TABLE user ADD COLUMN home_domain_id INTEGER '
                'REFERENCES domain(id) ON DELETE CASCADE'))
            conn.execute(text(
                'CREATE INDEX IF NOT EXISTS ix_user_home_domain_id '
                'ON user(home_domain_id)'))
        logger.info('schema: added user.home_domain_id')
        backfill_home_domain_from_roles()

    # Drop legacy global-unique indexes from older installs (SQLite).
    with db.engine.begin() as conn:
        for idx in insp.get_indexes('user'):
            name = idx.get('name') or ''
            cols = idx.get('column_names') or []
            unique = idx.get('unique', False)
            if not unique:
                continue
            if cols == ['username'] or cols == ['email']:
                try:
                    conn.execute(text(f'DROP INDEX IF EXISTS "{name}"'))
                    logger.info(f'schema: dropped legacy user index {name}')
                except Exception:
                    pass
        conn.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tenant_username '
            'ON user(home_domain_id, username)'))
        conn.execute(text(
            'CREATE UNIQUE INDEX IF NOT EXISTS uq_user_tenant_email '
            'ON user(home_domain_id, email)'))
