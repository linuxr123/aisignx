from datetime import datetime, timedelta

from flask import Blueprint, render_template
from flask_login import current_user, login_required
from sqlalchemy import func

from models import (
    AuditLog, Display, DisplayGroup, Domain, EmergencyBroadcast, Media,
    PendingDisplay, Playlist, Schedule, UserDomainRole, db,
)
from tenant_filter import bypass_tenant_filter, current_domain_id
from utils import is_online

main_bp = Blueprint('main', __name__)


def _human_bytes(num):
    if num is None or num <= 0:
        return '0 B'
    n = float(num)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024.0:
            if unit == 'B':
                return f'{int(n)} B'
            return f'{n:.1f} {unit}'
        n /= 1024.0
    return f'{n:.1f} TB'


def _storage_bar(used, quota):
    if not quota or quota <= 0:
        return None
    pct = min(100, round(100 * (used or 0) / quota, 1))
    return pct


@main_bp.route('/dashboard')
@login_required
def dashboard():
    """Tenant-scoped overview for the active workspace."""
    did = current_domain_id()
    domain = None
    tenant_slug = None
    tenant_timezone = None
    storage_used = 0
    storage_quota = None

    if did is not None:
        with bypass_tenant_filter():  # tenant-ok: load active tenant metadata
            domain = db.session.get(Domain, did)
        if domain:
            tenant_slug = domain.slug
            tenant_timezone = domain.default_timezone
            storage_used = domain.storage_used_bytes or 0
            storage_quota = domain.storage_quota_bytes

    displays = Display.query.order_by(Display.last_ping.desc()).all()
    display_count = len(displays)
    online_count = sum(1 for d in displays if is_online(d.last_ping))
    offline_count = display_count - online_count
    online_pct = round(100 * online_count / display_count, 1) if display_count else 0

    for d in displays:
        d.is_online = is_online(d.last_ping)

    recent_displays = displays[:10]
    offline_displays = [d for d in displays if not d.is_online][:6]

    group_count = DisplayGroup.query.count()
    media_count = Media.query.count()
    playlist_count = Playlist.query.count()
    schedule_count = Schedule.query.count()
    active_schedule_count = Schedule.query.filter_by(is_active=True).count()

    media_by_type = {}
    try:
        rows = (
            db.session.query(Media.media_type, func.count(Media.id))
            .group_by(Media.media_type)
            .all()
        )
        media_by_type = {str(t or 'other'): c for t, c in rows}
    except Exception:
        pass

    pending_count = 0
    if did is not None:
        pending_count = PendingDisplay.query.filter_by(
            status='pending', domain_id=did,
        ).count()

    member_count = 0
    if did is not None:
        with bypass_tenant_filter():  # tenant-ok: membership count
            member_count = UserDomainRole.query.filter_by(domain_id=did).count()

    conflict_count = 0
    try:
        from schedule_conflicts import compute_conflicts
        report = compute_conflicts(days_ahead=7)
        conflict_count = int(report.get('total_conflicts') or 0)
    except Exception:
        pass

    emergency = EmergencyBroadcast.query.filter_by(is_active=True).first()

    pop_enabled = False
    pop_24h = 0
    try:
        import proof_of_play as pop
        pop_enabled = pop.is_enabled()
        if pop_enabled:
            since = datetime.utcnow() - timedelta(hours=24)
            pop_24h = len(pop.query_for_current_domain(since=since, limit=10000))
    except Exception:
        pass

    recent_audit = []
    if did is not None:
        with bypass_tenant_filter():  # tenant-ok: audit rows keyed by domain_id
            recent_audit = (
                AuditLog.query.filter_by(domain_id=did)
                .order_by(AuditLog.timestamp.desc())
                .limit(8)
                .all()
            )

    storage_label = _human_bytes(storage_used)
    quota_label = _human_bytes(storage_quota) if storage_quota else '—'
    storage_pct = _storage_bar(storage_used, storage_quota)

    from domains import branding_state, domain_switcher_state
    branding = branding_state()
    switcher = domain_switcher_state()

    return render_template(
        'dashboard.html',
        tenant_id=did,
        tenant_name=branding.get('tenant_name') or switcher.get('current_name'),
        tenant_slug=tenant_slug,
        tenant_timezone=tenant_timezone,
        switcher=switcher,
        display_count=display_count,
        online_count=online_count,
        offline_count=offline_count,
        online_pct=online_pct,
        group_count=group_count,
        media_count=media_count,
        media_by_type=media_by_type,
        playlist_count=playlist_count,
        schedule_count=schedule_count,
        active_schedule_count=active_schedule_count,
        pending_count=pending_count,
        member_count=member_count,
        conflict_count=conflict_count,
        recent_displays=recent_displays,
        offline_displays=offline_displays,
        emergency=emergency,
        pop_enabled=pop_enabled,
        pop_24h=pop_24h,
        recent_audit=recent_audit,
        storage_used_label=storage_label,
        storage_quota_label=quota_label,
        storage_pct=storage_pct,
        storage_has_quota=bool(storage_quota),
        is_admin=bool(getattr(current_user, 'is_admin', False)),
        is_superadmin=bool(getattr(current_user, 'is_superadmin', False)),
        generated_at=datetime.utcnow(),
    )
