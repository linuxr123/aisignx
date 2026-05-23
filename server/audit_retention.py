"""
Audit log retention - Phase 4.

Periodic sweep that deletes old AuditLog rows according to a configurable
policy. Three knobs (all in `settings.BUILTIN_DEFAULTS`):

    audit.retention.enabled           bool   master switch
    audit.retention.default_days      int    fallback max age (0 = keep forever)
    audit.retention.overrides         json   {"action": days, "prefix.": days}
    audit.retention.purge_interval_hours int  sweep frequency (default 24h)
    audit.retention.batch_size        int    max rows deleted per action per pass

A retention value of 0 means "never delete" -- the action is skipped. A
trailing dot in an override key marks it as a prefix match (e.g. `"domain."`
covers `domain.create`, `domain.delete`, ...). Exact-action overrides win
over prefix overrides; longest matching prefix wins among prefixes.

Each sweep writes ONE summary audit row (`audit.retention.purge`) with the
total deleted count and per-action breakdown. The summary itself is exempt
from retention by being a system-scoped row (domain_id=None) -- if you want
to prune the summaries too, add a `"audit.retention.": N` override.
"""
from datetime import datetime, timedelta

from sqlalchemy import func

from logging_config import logger
from models import db, AuditLog
from tenant_filter import bypass_tenant_filter
import settings as settings_module


_JOB_NAME = 'audit-retention-sweep'


def _resolve_overrides(raw):
    """Split the overrides dict into (exact, prefixes_sorted_desc).
    `prefixes_sorted_desc` is a list of (prefix, days) sorted by length so the
    longest prefix wins."""
    exact = {}
    prefixes = []
    if not isinstance(raw, dict):
        return exact, prefixes
    for k, v in raw.items():
        if not isinstance(k, str):
            continue
        try:
            days = int(v)
        except (TypeError, ValueError):
            continue
        if days < 0:
            continue
        if k.endswith('.'):
            prefixes.append((k, days))
        else:
            exact[k] = days
    prefixes.sort(key=lambda p: len(p[0]), reverse=True)
    return exact, prefixes


def _retention_for(action, default_days, exact, prefixes):
    """Return the retention (in days) for one action, or None to skip."""
    if action in exact:
        days = exact[action]
    else:
        days = None
        for prefix, pdays in prefixes:
            if action.startswith(prefix):
                days = pdays
                break
        if days is None:
            days = default_days
    if days <= 0:
        return None
    return days


def purge_now():
    """One sweep. Safe to call manually (admin endpoint, tests, cron). Returns
    a dict {action: deleted_count} (totals across every tenant scope) for
    diagnostics; never raises.

    Per-tenant overrides: settings can be written at `domain_id=N` scope and
    they take precedence over the global value when pruning that tenant's
    rows. Each scope (global + every domain that has at least one
    audit.retention.* override) is swept independently."""
    deleted_by_action = {}
    try:
        if not settings_module.effective_value('audit.retention.enabled'):
            return deleted_by_action

        # Discover which domains have a per-tenant override on either of the
        # two effective-config keys. We always include the "global" scope
        # (None) for rows that don't belong to any tenant or that belong to
        # tenants with no override.
        from models import SystemSetting
        with bypass_tenant_filter():    # tenant-ok: scope discovery
            override_domain_ids = {
                r[0] for r in (
                    db.session.query(SystemSetting.domain_id)
                    .filter(SystemSetting.key.in_([
                        'audit.retention.default_days',
                        'audit.retention.overrides',
                        'audit.retention.enabled',
                    ]))
                    .filter(SystemSetting.domain_id.isnot(None))
                    .distinct().all())
            }

        # Scope iteration order: global first, then any per-tenant scopes.
        # `scope_did=None` means "all rows not handled by a domain-specific
        # scope below"; we filter those rows in the per-tenant pass.
        scopes = [None] + sorted(override_domain_ids)

        for scope_did in scopes:
            # Per-tenant kill switch lets a domain admin disable pruning
            # for their own tenant without touching the global default.
            if scope_did is not None and not settings_module.effective_value(
                    'audit.retention.enabled', domain_id=scope_did):
                continue

            default_days = int(settings_module.effective_value(
                'audit.retention.default_days', domain_id=scope_did) or 0)
            overrides_raw = settings_module.effective_value(
                'audit.retention.overrides', domain_id=scope_did) or {}
            batch_size = max(1, int(settings_module.effective_value(
                'audit.retention.batch_size', domain_id=scope_did) or 5000))

            exact, prefixes = _resolve_overrides(overrides_raw)

            if (default_days <= 0
                    and not any(d > 0 for d in exact.values())
                    and not any(d > 0 for _, d in prefixes)):
                continue

            with bypass_tenant_filter():    # tenant-ok: retention sweeps explicit scope
                base_q = db.session.query(AuditLog.action).distinct()
                if scope_did is None:
                    # Rows not covered by any per-tenant scope: NULL or any
                    # domain that has no overrides of its own.
                    if override_domain_ids:
                        base_q = base_q.filter(
                            (AuditLog.domain_id.is_(None)) |
                            (~AuditLog.domain_id.in_(override_domain_ids)))
                else:
                    base_q = base_q.filter(AuditLog.domain_id == scope_did)
                actions = [r[0] for r in base_q.all()]

                for action in actions:
                    if action == 'audit.retention.purge':
                        if action not in exact and not any(
                                action.startswith(p) for p, _ in prefixes):
                            continue
                    days = _retention_for(action, default_days, exact, prefixes)
                    if days is None:
                        continue
                    cutoff = datetime.utcnow() - timedelta(days=days)
                    id_q = (db.session.query(AuditLog.id)
                            .filter(AuditLog.action == action,
                                    AuditLog.timestamp < cutoff))
                    if scope_did is None:
                        if override_domain_ids:
                            id_q = id_q.filter(
                                (AuditLog.domain_id.is_(None)) |
                                (~AuditLog.domain_id.in_(override_domain_ids)))
                    else:
                        id_q = id_q.filter(AuditLog.domain_id == scope_did)
                    ids = [r[0] for r in (id_q
                                          .order_by(AuditLog.timestamp.asc())
                                          .limit(batch_size).all())]
                    if not ids:
                        continue
                    (AuditLog.query
                     .filter(AuditLog.id.in_(ids))
                     .delete(synchronize_session=False))
                    deleted_by_action[action] = (
                        deleted_by_action.get(action, 0) + len(ids))
                if deleted_by_action:
                    db.session.commit()

        if deleted_by_action:
            total = sum(deleted_by_action.values())
            logger.info(f'audit-retention: purged {total} row(s) across '
                        f'{len(deleted_by_action)} action(s).')
            try:
                from audit import audit
                audit('audit.retention.purge',
                      payload={'total': total, 'by_action': deleted_by_action})
            except Exception:
                logger.exception('audit-retention: failed to write summary audit row')
    except Exception:
        logger.exception('audit-retention: sweep failed')
        try:
            db.session.rollback()
        except Exception:
            pass
    return deleted_by_action


def install():
    """Register the periodic sweep. Idempotent w.r.t. duplicate calls only when
    the caller checks; jobs.schedule_periodic itself does not dedupe by name,
    so call this exactly once at startup."""
    from jobs import schedule_periodic
    hours = int(settings_module.effective_value(
        'audit.retention.purge_interval_hours') or 24)
    hours = max(1, hours)
    schedule_periodic(purge_now, every_s=hours * 3600.0, name=_JOB_NAME)
