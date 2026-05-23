"""
Proof of Play - Phase 4 (optional).

Lightweight playback-evidence trail. The display player POSTs one record per
slide it finishes; the server stores it tenant-scoped, and a periodic sweep
prunes old rows according to `proof_of_play.retention_days`.

Design notes:
  * Off by default. Enable per-deployment via `proof_of_play.enabled`.
  * Append-only. We never mutate rows after insert (audit-friendly).
  * Tenant-scoped via TenantModel + tenant_filter; admin endpoints honor
    the active domain.
  * Retention is a separate periodic job from audit-log retention so the
    two cadences can be tuned independently.
"""
from datetime import datetime, timedelta

from logging_config import logger
from models import db, ProofOfPlay, Display
from tenant_filter import bypass_tenant_filter, current_domain_id
import settings as settings_module


_JOB_NAME = 'proof-of-play-sweep'


def is_enabled() -> bool:
    return bool(settings_module.effective_value('proof_of_play.enabled'))


def record(*, display, item_type=None, item_name=None,
           media_id=None, playlist_id=None, plugin_key=None,
           duration_ms=None, completed=True, started_at=None) -> bool:
    """Insert one Proof-of-Play row. No-op when the feature is disabled or
    when `duration_ms` is below the configured floor. Never raises."""
    if not is_enabled():
        return False
    try:
        floor = int(settings_module.effective_value('proof_of_play.min_duration_ms') or 0)
    except (TypeError, ValueError):
        floor = 0
    if duration_ms is not None and duration_ms < floor:
        return False
    try:
        row = ProofOfPlay(
            domain_id   = display.domain_id,
            display_id  = display.id,
            media_id    = media_id,
            playlist_id = playlist_id,
            item_type   = item_type,
            item_name   = (item_name or '')[:255] if item_name else None,
            plugin_key  = plugin_key,
            duration_ms = duration_ms,
            completed   = bool(completed),
            started_at  = started_at or datetime.utcnow(),
            server_received_at = datetime.utcnow(),
        )
        with bypass_tenant_filter():       # tenant-ok: we set domain_id from display
            db.session.add(row)
            db.session.commit()
        return True
    except Exception:
        logger.exception('proof_of_play.record: insert failed')
        try:
            db.session.rollback()
        except Exception:
            pass
        return False


def purge_now() -> int:
    """One retention sweep. Returns the number of rows deleted. Never raises."""
    try:
        days = int(settings_module.effective_value('proof_of_play.retention_days') or 0)
    except (TypeError, ValueError):
        days = 0
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted = 0
    try:
        with bypass_tenant_filter():       # tenant-ok: retention sweeps every domain
            # Bounded batch via id select (same pattern as audit_retention).
            ids = [r[0] for r in (db.session.query(ProofOfPlay.id)
                                   .filter(ProofOfPlay.started_at < cutoff)
                                   .order_by(ProofOfPlay.started_at.asc())
                                   .limit(10000)
                                   .all())]
            if ids:
                deleted = (ProofOfPlay.query
                           .filter(ProofOfPlay.id.in_(ids))
                           .delete(synchronize_session=False))
                db.session.commit()
        if deleted:
            logger.info(f'proof_of_play: purged {deleted} row(s) older than {days} day(s).')
    except Exception:
        logger.exception('proof_of_play.purge_now: sweep failed')
        try:
            db.session.rollback()
        except Exception:
            pass
    return deleted


def install():
    """Register the periodic sweep. Call exactly once at startup."""
    from jobs import schedule_periodic
    # Run daily; cheap when disabled (purge_now bails on retention<=0).
    schedule_periodic(purge_now, every_s=24 * 3600.0, name=_JOB_NAME)


# ---------------------------------------------------------------------------
# Query helpers (used by admin views / CSV export).
# ---------------------------------------------------------------------------
def _apply_domain_scope(q, *, scope_all=False, domain_id=None, domain_ids=None):
    """Restrict ProofOfPlay rows by tenant. Caller may already be bypassed."""
    if scope_all:
        if domain_id is not None:
            q = q.filter(ProofOfPlay.domain_id == int(domain_id))
        return q
    if domain_ids is not None:
        ids = [int(i) for i in domain_ids if i is not None]
        if not ids:
            return q.filter(ProofOfPlay.domain_id == -1)
        if domain_id is not None:
            did = int(domain_id)
            if did not in ids:
                return q.filter(ProofOfPlay.domain_id == -1)
            return q.filter(ProofOfPlay.domain_id == did)
        if len(ids) == 1:
            return q.filter(ProofOfPlay.domain_id == ids[0])
        return q.filter(ProofOfPlay.domain_id.in_(ids))
    if domain_id is not None:
        q = q.filter(ProofOfPlay.domain_id == int(domain_id))
    return q


def query_events(*, since=None, until=None,
                 display_ids=None, item_type=None,
                 plugin_key=None, limit=1000,
                 scope_all=False, domain_id=None, domain_ids=None):
    """Return ProofOfPlay rows, newest first.

    Superadmins may pass scope_all=True to read every tenant. Tenant admins
    pass domain_ids (allowed tenants). display_ids accepts display primary keys.
    """
    def _run():
        q = ProofOfPlay.query
        q = _apply_domain_scope(q, scope_all=scope_all,
                                domain_id=domain_id, domain_ids=domain_ids)
        if since is not None:
            q = q.filter(ProofOfPlay.started_at >= since)
        if until is not None:
            q = q.filter(ProofOfPlay.started_at < until)
        if display_ids:
            ids = [int(i) for i in display_ids if i is not None]
            if ids:
                q = q.filter(ProofOfPlay.display_id.in_(ids))
        if item_type:
            q = q.filter(ProofOfPlay.item_type == item_type)
        if plugin_key:
            q = q.filter(ProofOfPlay.plugin_key == plugin_key)
        q = q.order_by(ProofOfPlay.started_at.desc())
        if limit:
            q = q.limit(int(limit))
        return q.all()

    if scope_all or domain_ids is not None:
        with bypass_tenant_filter():       # tenant-ok: explicit PoP tenant scope
            return _run()
    return _run()


def query_for_current_domain(*, since=None, until=None,
                             display_id=None, item_type=None,
                             plugin_key=None, limit=1000):
    """Return ProofOfPlay rows for the active tenant, newest first."""
    display_ids = [display_id] if display_id is not None else None
    return query_events(
        since=since, until=until,
        display_ids=display_ids,
        item_type=item_type, plugin_key=plugin_key,
        limit=limit, scope_all=False,
    )


def filter_options(*, scope_all=False, domain_id=None, domain_ids=None):
    """Distinct media types and plugin keys seen in PoP for filter dropdowns."""

    def _run():
        tq = (db.session.query(ProofOfPlay.item_type)
              .filter(ProofOfPlay.item_type.isnot(None),
                      ProofOfPlay.item_type != ''))
        tq = _apply_domain_scope(tq, scope_all=scope_all,
                                 domain_id=domain_id, domain_ids=domain_ids)
        item_types = sorted({r[0] for r in tq.distinct().all() if r[0]})

        pq = (db.session.query(ProofOfPlay.plugin_key)
              .filter(ProofOfPlay.plugin_key.isnot(None),
                      ProofOfPlay.plugin_key != ''))
        pq = _apply_domain_scope(pq, scope_all=scope_all,
                                 domain_id=domain_id, domain_ids=domain_ids)
        plugins = sorted({r[0] for r in pq.distinct().all() if r[0]})
        return {'item_types': item_types, 'plugins': plugins}

    if scope_all or domain_ids is not None:
        with bypass_tenant_filter():       # tenant-ok: explicit PoP filter lists
            return _run()
    return _run()


def display_belongs_to_current_domain(token: str):
    """Resolve a display by its api_key without leaking cross-tenant rows.
    Used by the player ingest endpoint, which is token-gated (no session)."""
    with bypass_tenant_filter():           # tenant-ok: token is the auth
        d = Display.query.filter_by(api_key=token).first()
    return d
