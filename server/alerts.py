"""
Display offline alerts.

Periodic background job that watches every Display's last_ping and emits
notifications when:
  - a display has been offline (no ping) for at least
    `alerts.offline_threshold_min` minutes, AND
  - the server has not yet sent an offline alert for that outage, AND
  - the display's domain is configured with at least one delivery
    target (email or webhook).

When the display starts pinging again, a single "recovered" notification
is emitted so on-call staff know the alert has cleared.

Delivery channels:
  - email   : SMTP via `alerts.smtp_*` settings (best-effort; failures
              are logged but never raised so one bad relay doesn't break
              the whole sweep).
  - webhook : HTTP POST of a small JSON envelope. Suitable for Slack
              incoming-webhooks, Microsoft Teams, PagerDuty Events v2 (with
              a small adapter), or a custom endpoint.

Audit trail:
  - alerts.display_offline   : alert dispatched
  - alerts.display_recovered : recovery dispatched
  - alerts.delivery_failed   : per-channel failure (email or webhook)

State storage:
  - In-process dict of {display_id: {'alerted_at': ts}} so we never send
    twice for the same outage. State is rebuilt from the audit log on
    boot so a restart doesn't re-spam every still-offline display.

This module never modifies playback or fleet state -- it is read-only
with respect to the display lifecycle.
"""
import json
import logging
import smtplib
import time
from email.message import EmailMessage
from urllib import request as urlrequest, error as urlerror

import settings
from audit import audit
from jobs import schedule_periodic
from models import Display, AuditLog, db
from tenant_filter import bypass_tenant_filter
from utils import is_online


logger = logging.getLogger(__name__)


# How often the sweep runs. Must be small relative to the offline
# threshold or alerts will lag noticeably.
SWEEP_INTERVAL_S = 60

# Per-display alert state. Rebuilt from the audit log on first sweep.
# Shape: {display_id: {'alerted_at': float}}
_alert_state: dict[int, dict] = {}
_state_loaded = False

# Per-display snooze state. Suppresses *new* offline alerts (and the
# matching recovery notification) until the snooze expires. Recovery
# notifications still fire if a snooze window ended *before* the
# display came back -- the operator only gave us permission to skip
# the alert, not to lie about state. Shape: {display_id: epoch_expiry}
_snoozed: dict[int, float] = {}
_snooze_reason: dict[int, str] = {}


def snooze_display(display_id: int, hours: float, reason: str = '') -> dict:
    """Suppress offline alerts for one display for `hours` hours.
    Pass hours <= 0 to clear an existing snooze."""
    did = int(display_id)
    if hours <= 0:
        was = _snoozed.pop(did, None)
        _snooze_reason.pop(did, None)
        audit('alerts.snooze_cleared', target_type='display',
              target_id=did, payload={'previous_until': was})
        return {'display_id': did, 'snoozed_until': None}
    until = time.time() + (hours * 3600.0)
    _snoozed[did] = until
    if reason:
        _snooze_reason[did] = reason
    else:
        _snooze_reason.pop(did, None)
    # If the display is currently in an outage, drop the active state so
    # the recovery notification isn't fired the moment the snooze ends.
    _alert_state.pop(did, None)
    audit('alerts.snoozed', target_type='display', target_id=did,
          payload={'hours': hours, 'until': until, 'reason': reason})
    return {'display_id': did, 'snoozed_until': until}


def snoozes() -> dict[int, float]:
    """Return a snapshot of active snoozes; expired entries are pruned."""
    now = time.time()
    expired = [k for k, v in _snoozed.items() if v <= now]
    for k in expired:
        _snoozed.pop(k, None)
        _snooze_reason.pop(k, None)
    return dict(_snoozed)


def snooze_info() -> dict[int, dict]:
    """Like snoozes() but also includes the reason text per display."""
    active = snoozes()
    return {did: {'until': until, 'reason': _snooze_reason.get(did, '')}
            for did, until in active.items()}


# ── Auto-snooze schedules ────────────────────────────────────────────────────
# Schedules are stored as a JSON string in the 'alerts.auto_snooze_schedules'
# setting. Cheap to evaluate (typical install: a handful of windows) so we
# parse fresh on every sweep — keeps the admin UI changes effective without
# a process restart.
def _load_schedules(domain_id=None) -> list[dict]:
    raw = (_alert_cfg('alerts.auto_snooze_schedules', domain_id) or '').strip()
    if not raw:
        return []
    try:
        import json as _json
        out = _json.loads(raw)
        return out if isinstance(out, list) else []
    except Exception:
        logger.warning('alerts: bad auto_snooze_schedules JSON; ignoring.')
        return []


def _schedule_active_now(s: dict, now_utc: float | None = None) -> bool:
    """Return True if schedule entry s is currently in its window.
    Uses the window's tz_offset_min (minutes east of UTC; default 0 = UTC)
    so each window can be expressed in store-local time without the
    server's clock having to match."""
    if now_utc is None:
        now_utc = time.time()
    tz_min = int(s.get('tz_offset_min') or 0)
    local_ts = now_utc + tz_min * 60
    local = time.gmtime(local_ts)            # gmtime on offset-shifted ts = local
    # weekday(): time.struct_time exposes tm_wday with Mon=0..Sun=6.
    days = s.get('days') or list(range(7))
    if local.tm_wday not in days:
        return False
    try:
        sh, sm = [int(x) for x in str(s.get('start_hm', '00:00')).split(':')]
        eh, em = [int(x) for x in str(s.get('end_hm',   '00:00')).split(':')]
    except (TypeError, ValueError):
        return False
    cur_min = local.tm_hour * 60 + local.tm_min
    start   = sh * 60 + sm
    end     = eh * 60 + em
    if start == end:
        return False
    if start < end:
        return start <= cur_min < end
    # Window crosses midnight (e.g. 22:00 → 06:00).
    return cur_min >= start or cur_min < end


def _scheduled_display_ids_for_domain(domain_id, now_utc: float | None = None) -> set[int]:
    """Return display ids in an auto-snooze window for one tenant."""
    schedules = _load_schedules(domain_id)
    if not schedules:
        return set()
    active = [s for s in schedules if _schedule_active_now(s, now_utc)]
    if not active:
        return set()
    # Resolve targets. We import here to avoid a hard dep at module load.
    out: set[int] = set()
    group_ids: set[int] = set()
    explicit_globals = False
    for s in active:
        did = s.get('display_id')
        gid = s.get('group_id')
        if did is None and gid is None:
            explicit_globals = True
        if did is not None:
            try:
                out.add(int(did))
            except (TypeError, ValueError):
                pass
        if gid is not None:
            try:
                group_ids.add(int(gid))
            except (TypeError, ValueError):
                pass
    if group_ids or explicit_globals:
        with bypass_tenant_filter():    # tenant-ok: schedules evaluated per tenant
            q = Display.query.with_entities(Display.id, Display.group_id)
            if domain_id is not None:
                q = q.filter(Display.domain_id == domain_id)
            if explicit_globals:
                rows = q.all()
                out.update(r.id for r in rows)
            elif group_ids:
                rows = q.filter(Display.group_id.in_(group_ids)).all()
                out.update(r.id for r in rows)
    return out


# ── Settings registration ────────────────────────────────────────────────────
# Settings are registered by inserting into settings.BUILTIN_DEFAULTS at
# import time. Done here (rather than editing settings.py directly) so the
# alerts module is fully self-contained: removing this file removes the
# feature without leaving orphan defaults.
_ALERT_DEFAULTS = {
    'alerts.enabled': (
        False, 'bool', False,
        'Master switch for display offline/recovery alerts. Off by default.',
    ),
    'alerts.offline_threshold_min': (
        10, 'int', False,
        'How many minutes a display must be missing pings before an offline '
        'alert is dispatched. Should be larger than the heartbeat batch '
        'window so transient flushes do not trip false positives.',
    ),
    'alerts.email_to': (
        '', 'string', False,
        'Deprecated. Alert email recipients are assigned to users with '
        'alerts.user_recipients.',
    ),
    'alerts.user_recipients': (
        {'global_user_ids': [], 'tenant_user_ids': {}, 'alert_types': {}}, 'json', False,
        'Alert recipient assignments. global_user_ids receive global/system '
        'alerts; tenant_user_ids maps tenant id to user ids that receive '
        'alerts scoped to that tenant. alert_types maps recipient keys to '
        'enabled categories. (Superadmin global setting.)',
    ),
    'alerts.tenant_recipients': (
        {'user_ids': [], 'alert_types': {}}, 'json', False,
        'Per-tenant alert recipients managed by the tenant admin. user_ids '
        'are members of this tenant; alert_types maps tenant:<user_id> keys '
        'to enabled categories (display, security, disk, digest).',
    ),
    'alerts.email_from': (
        '', 'string', False,
        'From address used on alert emails.',
    ),
    'alerts.smtp_host': (
        '', 'string', False, 'SMTP relay hostname (empty disables email).',
    ),
    'alerts.smtp_port': (
        587, 'int', False, 'SMTP relay port. Common: 25, 465 (SSL), 587 (STARTTLS).',
    ),
    'alerts.smtp_user': (
        '', 'string', False, 'SMTP auth username (empty for no auth).',
    ),
    'alerts.smtp_password': (
        '', 'string', True, 'SMTP auth password.',
    ),
    'alerts.smtp_starttls': (
        True, 'bool', False, 'Use STARTTLS on the SMTP connection.',
    ),
    'alerts.webhook_url': (
        '', 'string', False,
        'POST endpoint that receives a small JSON payload on each alert. '
        'Compatible with Slack/Teams incoming webhooks. Empty disables.',
    ),
    'alerts.duplicate_client_enabled': (
        True, 'bool', False,
        'When true, notify when a second client is blocked from using the '
        'same display API key.',
    ),
    'alerts.bad_login_enabled': (
        True, 'bool', False,
        'When true, notify when login attempts are rejected by the rate limiter.',
    ),
    'alerts.security_event_throttle_min': (
        5, 'int', False,
        'Minimum minutes between duplicate-client or bad-login notifications '
        'for the same source. Audit entries use the same throttle to avoid '
        'runaway clients filling the log.',
    ),
    'alerts.auto_snooze_schedules': (
        '[]', 'string', False,
        'JSON array of auto-snooze schedules. Each entry is '
        '{"display_id": int|null, "group_id": int|null, "days": [0-6], '
        '"start_hm": "HH:MM", "end_hm": "HH:MM", "tz_offset_min": int}. '
        'During an active window the named display(s) will not generate '
        'offline notifications. display_id=null + group_id=null = global.',
    ),
    'alerts.digest_enabled': (
        False, 'bool', False,
        'If true, send a daily roll-up email of alert activity in addition '
        'to (or instead of) per-event alerts.',
    ),
    'alerts.digest_hour': (
        8, 'int', False,
        'Local hour (0-23) at which the daily digest is delivered.',
    ),
    'alerts.digest_only': (
        False, 'bool', False,
        'If true, suppress per-event alerts entirely; deliver only the '
        'daily digest. Useful for low-priority fleets.',
    ),
    'alerts.digest_last_sent': (
        '', 'string', False,
        'Internal: ISO date of the last successful digest send; used to '
        'avoid double-firing within the same day.',
    ),
}
for _k, _v in _ALERT_DEFAULTS.items():
    settings.BUILTIN_DEFAULTS.setdefault(_k, _v)


def _alert_cfg(key, domain_id=None):
    """Resolve an alerts.* setting with optional per-tenant override."""
    return settings.effective_value(key, domain_id=domain_id)


# ── State ────────────────────────────────────────────────────────────────────
_security_event_state: dict[str, float] = {}
ALERT_TYPE_CHOICES = {
    'display': 'Display outages and recoveries',
    'security': 'Duplicate clients and blocked logins',
    'disk': 'Disk warnings and recoveries',
    'digest': 'Daily digest',
}
_DEFAULT_ALERT_TYPES = set(ALERT_TYPE_CHOICES.keys())


def _security_throttle_seconds(domain_id: int | None = None) -> int:
    try:
        minutes = int(_alert_cfg('alerts.security_event_throttle_min', domain_id) or 5)
    except (TypeError, ValueError):
        minutes = 5
    return max(1, minutes) * 60


def notify_event(kind: str, subject: str, body: str, payload: dict | None = None,
                 target_type: str | None = None, target_id=None,
                 domain_id: int | None = None, throttle_key: str | None = None,
                 alert_type: str = 'security') -> dict:
    """Write and optionally deliver an alert event that is not part of the
    offline sweep, such as duplicate clients or blocked login bursts."""
    action = f'alerts.{kind}'
    payload = dict(payload or {})
    throttle_id = throttle_key or f'{kind}:{target_type or ""}:{target_id or ""}'
    now = time.time()
    last = _security_event_state.get(throttle_id)
    if last and (now - last) < _security_throttle_seconds(domain_id):
        return {'sent': False, 'throttled': True}
    _security_event_state[throttle_id] = now

    audit(action, target_type=target_type, target_id=target_id,
          payload=payload, domain_id=domain_id)

    if not bool(_alert_cfg('alerts.enabled', domain_id)):
        return {'sent': False, 'disabled': True}

    if bool(_alert_cfg('alerts.digest_only', domain_id)):
        return {'sent': False, 'digest_only': True}

    event_payload = {
        'event': kind,
        'text': body.splitlines()[0] if body else subject,
        **payload,
    }
    ok_email, err_email = _send_email(subject, body, domain_id=domain_id,
                                      alert_type=alert_type)
    ok_hook, err_hook = _send_webhook(event_payload, domain_id=domain_id)
    if not ok_email and err_email and err_email != 'email not configured':
        audit('alerts.delivery_failed', target_type=target_type,
              target_id=target_id, domain_id=domain_id,
              payload={'channel': 'email', 'error': err_email, 'kind': kind})
    if not ok_hook and err_hook and err_hook != 'webhook not configured':
        audit('alerts.delivery_failed', target_type=target_type,
              target_id=target_id, domain_id=domain_id,
              payload={'channel': 'webhook', 'error': err_hook, 'kind': kind})
    return {
        'sent': bool(ok_email or ok_hook),
        'email_ok': ok_email,
        'webhook_ok': ok_hook,
        'email_error': err_email,
        'webhook_error': err_hook,
    }


def _load_state_from_audit():
    """Reconstruct in-memory alert state from the audit log so a restart
    does not re-spam alerts for still-offline displays. We look for the
    most recent alerts.display_offline / display_recovered per display.
    """
    global _state_loaded
    try:
        with bypass_tenant_filter():    # tenant-ok: cross-tenant alert state
            rows = (AuditLog.query
                    .filter(AuditLog.action.in_(
                        ['alerts.display_offline', 'alerts.display_recovered']))
                    .order_by(AuditLog.id.desc())
                    .limit(2000)
                    .all())
        seen = set()
        for r in rows:
            tid = r.target_id
            if not tid or tid in seen:
                continue
            seen.add(tid)
            try:
                did = int(tid)
            except (TypeError, ValueError):
                continue
            if r.action == 'alerts.display_offline':
                _alert_state[did] = {'alerted_at': r.created_at.timestamp()
                                     if r.created_at else time.time()}
    except Exception as exc:
        logger.warning('alerts: failed to rebuild state from audit log: %s', exc)
    _state_loaded = True


# ── Delivery ─────────────────────────────────────────────────────────────────
def _recipient_type_enabled(cfg: dict, key: str, alert_type: str | None) -> bool:
    if not alert_type:
        return True
    raw_map = cfg.get('alert_types') or {}
    raw = raw_map.get(key)
    # Existing installs had no per-recipient types; keep those recipients
    # subscribed to all alert categories until an admin edits them.
    if raw is None:
        return True
    if not isinstance(raw, list):
        return False
    return alert_type in {str(x) for x in raw}


def _recipient_emails(domain_id: int | None = None,
                      alert_type: str | None = None) -> list[str]:
    emails = []
    cfg = settings.effective_value('alerts.user_recipients') or {}
    try:
        from models import User, UserDomainRole
        from tenant_filter import bypass_tenant_filter
        user_ids: set[int] = set()
        global_ids = [
            int(x) for x in (cfg.get('global_user_ids') or [])
            if str(x).isdigit()
        ]
        global_ids = [
            uid for uid in global_ids
            if _recipient_type_enabled(cfg, f'global:{uid}', alert_type)
        ]
        if domain_id is None:
            user_ids.update(global_ids)
        else:
            local_cfg = settings.effective_value('alerts.tenant_recipients',
                                                 domain_id=domain_id) or {}
            local_ids = [
                int(x) for x in (local_cfg.get('user_ids') or [])
                if str(x).isdigit()
            ]
            for uid in local_ids:
                if _recipient_type_enabled(local_cfg, f'tenant:{uid}', alert_type):
                    user_ids.add(uid)
            tenant_map = cfg.get('tenant_user_ids') or {}
            tenant_ids = [
                int(x) for x in (tenant_map.get(str(domain_id)) or [])
                if str(x).isdigit()
            ]
            tenant_ids = [
                uid for uid in tenant_ids
                if _recipient_type_enabled(cfg, f'tenant:{domain_id}:{uid}', alert_type)
            ]
            user_ids.update(tenant_ids)
            # Superuser/global recipients also receive tenant-scoped alerts.
            user_ids.update(global_ids)
        if user_ids:
            with bypass_tenant_filter():
                q = User.query.filter(User.id.in_(user_ids), User.active == True)
                if domain_id is not None:
                    allowed = {
                        r.user_id for r in UserDomainRole.query
                        .filter(UserDomainRole.domain_id == int(domain_id),
                                UserDomainRole.user_id.in_(user_ids))
                        .all()
                    }
                    q = q.filter((User.is_superadmin == True) | (User.id.in_(allowed)))
                emails.extend(u.email for u in q.all() if u.email)
    except Exception as exc:
        logger.warning('alerts: failed to resolve assigned recipients: %s', exc)
    # Preserve order while removing duplicates.
    out = []
    seen = set()
    for email in emails:
        key = email.lower()
        if key not in seen:
            seen.add(key)
            out.append(email)
    return out


def _send_email(subject: str, body: str, domain_id: int | None = None,
                alert_type: str | None = None) -> tuple[bool, str | None]:
    host = (_alert_cfg('alerts.smtp_host', domain_id) or '').strip()
    recipients = _recipient_emails(domain_id, alert_type=alert_type)
    to   = ', '.join(recipients)
    sender = (_alert_cfg('alerts.email_from', domain_id) or '').strip()
    if not host or not to or not sender:
        return False, 'email not configured'

    port    = int(_alert_cfg('alerts.smtp_port', domain_id) or 587)
    user    = (_alert_cfg('alerts.smtp_user', domain_id) or '').strip()
    pw      = (_alert_cfg('alerts.smtp_password', domain_id) or '')
    starttls = bool(_alert_cfg('alerts.smtp_starttls', domain_id))

    msg = EmailMessage()
    msg['From'] = sender
    msg['To']   = to
    msg['Subject'] = subject
    msg.set_content(body)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=15) as s:
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=15) as s:
                if starttls:
                    s.starttls()
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        return True, None
    except Exception as exc:
        return False, str(exc)


def _send_webhook(payload: dict, domain_id: int | None = None) -> tuple[bool, str | None]:
    url = (_alert_cfg('alerts.webhook_url', domain_id) or '').strip()
    if not url:
        return False, 'webhook not configured'
    data = json.dumps(payload).encode('utf-8')
    req = urlrequest.Request(
        url, data=data, method='POST',
        headers={'Content-Type': 'application/json',
                 'User-Agent': 'AISignX-Alerts/1.0'})
    try:
        with urlrequest.urlopen(req, timeout=10) as resp:
            if 200 <= resp.status < 300:
                return True, None
            return False, f'HTTP {resp.status}'
    except urlerror.HTTPError as exc:
        return False, f'HTTP {exc.code}'
    except Exception as exc:
        return False, str(exc)


def _dispatch(display: Display, kind: str, minutes_offline: float | None):
    """kind = 'offline' or 'recovered'."""
    name = display.name or f'Display #{display.id}'
    location = display.location or ''
    if kind == 'offline':
        subject = f'[AISignX] Display offline: {name}'
        line = f'{name} has been offline for {int(minutes_offline or 0)} minutes.'
    else:
        subject = f'[AISignX] Display recovered: {name}'
        line = f'{name} is reporting heartbeats again.'
    body = (f'{line}\n\n'
            f'Display ID : {display.id}\n'
            f'Location   : {location or "(not set)"}\n'
            f'Last ping  : {display.last_ping.isoformat() if display.last_ping else "never"}\n'
            f'IP address : {display.ip_address or "n/a"}\n')

    payload = {
        'event':           f'display_{kind}',
        'display_id':      display.id,
        'display_name':    name,
        'location':        location,
        'last_ping':       display.last_ping.isoformat() if display.last_ping else None,
        'minutes_offline': int(minutes_offline or 0),
        'ip_address':      display.ip_address,
        'text':            line,
    }

    # Email
    ok_email, err_email = _send_email(
        subject, body, domain_id=getattr(display, 'domain_id', None),
        alert_type='display')
    if not ok_email and err_email and err_email != 'email not configured':
        logger.warning('alerts: email delivery failed for display %s: %s',
                       display.id, err_email)
        audit('alerts.delivery_failed', target_type='display',
              target_id=display.id,
              payload={'channel': 'email', 'error': err_email, 'kind': kind})

    # Webhook
    did = getattr(display, 'domain_id', None)
    ok_hook, err_hook = _send_webhook(payload, domain_id=did)
    if not ok_hook and err_hook and err_hook != 'webhook not configured':
        logger.warning('alerts: webhook delivery failed for display %s: %s',
                       display.id, err_hook)
        audit('alerts.delivery_failed', target_type='display',
              target_id=display.id,
              payload={'channel': 'webhook', 'error': err_hook, 'kind': kind})


# ── Sweep ────────────────────────────────────────────────────────────────────
def sweep_now():
    """One pass over every display. Cheap; no joins, just last_ping."""
    if not _state_loaded:
        _load_state_from_audit()

    sent_offline = 0
    sent_recovered = 0
    snoozes_now = snoozes()
    scheduled_by_domain: dict[int, set[int]] = {}
    with bypass_tenant_filter():    # tenant-ok: alerts span every tenant
        displays = Display.query.with_entities(
            Display.id, Display.name, Display.location, Display.last_ping,
            Display.ip_address, Display.domain_id
        ).all()

        for row in displays:
            did = row.id
            tenant_id = row.domain_id
            if not bool(_alert_cfg('alerts.enabled', tenant_id)):
                continue
            threshold_min = int(_alert_cfg('alerts.offline_threshold_min', tenant_id) or 10)
            if threshold_min < 1:
                threshold_min = 1
            timeout_s = threshold_min * 60
            digest_only = bool(_alert_cfg('alerts.digest_only', tenant_id))
            offline = not is_online(row.last_ping, timeout_seconds=timeout_s)
            state   = _alert_state.get(did)
            now     = time.time()

            # Snoozed? Skip new alerts entirely. We also do NOT mark the
            # display as alerted, so when the snooze ends a real outage
            # will dispatch normally on the next sweep.
            if did in snoozes_now and offline and state is None:
                continue
            # Auto-snooze schedules suppress notifications the same way.
            if tenant_id is not None:
                if tenant_id not in scheduled_by_domain:
                    scheduled_by_domain[tenant_id] = (
                        _scheduled_display_ids_for_domain(tenant_id))
                scheduled_now = scheduled_by_domain[tenant_id]
            else:
                scheduled_now = set()
            if did in scheduled_now and offline and state is None:
                continue
            # In digest-only mode, still track outage state so the digest
            # has accurate numbers, but never call _dispatch().
            if digest_only:
                if offline and state is None:
                    _alert_state[did] = {'alerted_at': now}
                elif (not offline) and state is not None:
                    _alert_state.pop(did, None)
                continue

            if offline and state is None:
                # New outage. Re-fetch the full row so we can build a
                # nicer email body (includes name / location / etc.).
                d = Display.query.get(did)
                if not d:
                    continue
                minutes = (timeout_s / 60.0)
                if d.last_ping:
                    minutes = max(minutes,
                                  (now - d.last_ping.timestamp()) / 60.0)
                _dispatch(d, 'offline', minutes)
                _alert_state[did] = {'alerted_at': now}
                audit('alerts.display_offline', target_type='display',
                      target_id=d.id,
                      payload={'minutes_offline': int(minutes)})
                sent_offline += 1

            elif (not offline) and state is not None:
                d = Display.query.get(did)
                if d is None:
                    _alert_state.pop(did, None)
                    continue
                _dispatch(d, 'recovered', None)
                _alert_state.pop(did, None)
                audit('alerts.display_recovered', target_type='display',
                      target_id=d.id,
                      payload={'alerted_at': state.get('alerted_at')})
                sent_recovered += 1

    scheduled_count = sum(len(ids) for ids in scheduled_by_domain.values())
    return {
        'enabled':       True,
        'sent_offline':  sent_offline,
        'sent_recovered': sent_recovered,
        'tracking':      len(_alert_state),
        'snoozed':       len(snoozes_now),
        'scheduled':     scheduled_count,
    }


# ── Daily digest ────────────────────────────────────────────────────────────
# Runs every hour; sends only when local hour == digest_hour AND we have
# not already sent for today. The "last sent date" lives in a setting so
# it survives restarts.
def _digest_summary(since_iso: str) -> dict:
    """Aggregate alert events in the last 24h into a small dict."""
    by_action: dict[str, int] = {}
    offline_displays: dict[int, int] = {}    # display_id -> count
    recovered: set[int] = set()
    snoozed:   set[int] = set()
    failed = 0
    with bypass_tenant_filter():    # tenant-ok: cross-tenant digest
        rows = (AuditLog.query
                .filter(AuditLog.action.like('alerts.%'))
                .filter(AuditLog.timestamp >= _dt_parse(since_iso))
                .order_by(AuditLog.id.asc())
                .all())
        for r in rows:
            by_action[r.action] = by_action.get(r.action, 0) + 1
            if r.target_type == 'display' and r.target_id:
                try:
                    did = int(r.target_id)
                except (TypeError, ValueError):
                    continue
                if r.action == 'alerts.display_offline':
                    offline_displays[did] = offline_displays.get(did, 0) + 1
                elif r.action == 'alerts.display_recovered':
                    recovered.add(did)
                elif r.action == 'alerts.snoozed':
                    snoozed.add(did)
                elif r.action == 'alerts.delivery_failed':
                    failed += 1

        d_ids = set(offline_displays) | recovered | snoozed
        names = {d.id: d.name for d in
                 Display.query.filter(Display.id.in_(d_ids)).all()} if d_ids else {}

    top_offenders = sorted(offline_displays.items(),
                           key=lambda kv: kv[1], reverse=True)[:10]
    return {
        'since':            since_iso,
        'totals':           by_action,
        'offline_displays': len(offline_displays),
        'recovered':        len(recovered),
        'snoozed':          len(snoozed),
        'delivery_failures': failed,
        'top_offenders':    [{'id': did, 'name': names.get(did, f'#{did}'),
                              'offline_events': cnt}
                             for did, cnt in top_offenders],
        'currently_offline': len(_alert_state),
    }


def _dt_parse(iso_str: str):
    """Tolerant ISO parser; falls back to 'now - 24h' on failure."""
    import datetime as _dtm
    try:
        return _dtm.datetime.fromisoformat(iso_str.replace('Z', ''))
    except Exception:
        return _dtm.datetime.utcnow() - _dtm.timedelta(hours=24)


def _format_digest(summary: dict) -> tuple[str, str]:
    """Return (subject, body_text) for the digest email/webhook."""
    subject = (f"[AISignX] Daily digest — "
               f"{summary['offline_displays']} offline, "
               f"{summary['recovered']} recovered")
    lines = [
        f"Summary since {summary['since']}",
        '',
        f"  Offline displays    : {summary['offline_displays']}",
        f"  Recovered displays  : {summary['recovered']}",
        f"  Snoozed (new)       : {summary['snoozed']}",
        f"  Delivery failures   : {summary['delivery_failures']}",
        f"  Currently offline   : {summary['currently_offline']}",
        '',
    ]
    if summary['top_offenders']:
        lines.append('Top offenders (most offline events):')
        for o in summary['top_offenders']:
            lines.append(f"  {o['offline_events']:3d} × {o['name']} (id={o['id']})")
    else:
        lines.append('No offline events in the period.')
    return subject, '\n'.join(lines)


def digest_now(force: bool = False) -> dict:
    """Build + send the daily digest. With force=False, only sends when
    we are in the configured local hour and haven't already sent today.
    Always returns a status dict."""
    if not bool(settings.effective_value('alerts.enabled')):
        return {'sent': False, 'reason': 'alerts disabled'}
    if not bool(settings.effective_value('alerts.digest_enabled')):
        return {'sent': False, 'reason': 'digest disabled'}

    import datetime as _dtm
    now_utc = _dtm.datetime.utcnow()
    target_hour = int(settings.effective_value('alerts.digest_hour') or 8)
    if not force:
        if now_utc.hour != target_hour:
            return {'sent': False, 'reason': f'not digest hour ({target_hour:02d}:00 UTC)'}
        last = (settings.effective_value('alerts.digest_last_sent') or '').strip()
        today = now_utc.date().isoformat()
        if last == today:
            return {'sent': False, 'reason': 'already sent today'}

    since = (now_utc - _dtm.timedelta(hours=24)).isoformat() + 'Z'
    summary = _digest_summary(since)
    subject, body = _format_digest(summary)
    payload = {'event': 'daily_digest', 'subject': subject,
               'text': body, 'summary': summary}

    ok_e, err_e = _send_email(subject, body, alert_type='digest')
    ok_w, err_w = _send_webhook(payload)

    # Best-effort persist of last-sent date so we don't double-fire.
    try:
        settings.set('alerts.digest_last_sent', now_utc.date().isoformat())
    except Exception:
        pass

    audit('alerts.digest_sent', payload={
        'email_ok': ok_e, 'webhook_ok': ok_w,
        'email_error': err_e, 'webhook_error': err_w,
        'summary': summary,
    })
    return {'sent': True, 'email': {'ok': ok_e, 'error': err_e},
            'webhook': {'ok': ok_w, 'error': err_w},
            'summary': summary}


def install():
    """Register the periodic sweep. Idempotent w.r.t. duplicate calls
    only when the caller checks; jobs.schedule_periodic itself does
    not dedupe, so app.py guards this with `with app.app_context()`
    and a single call site."""
    schedule_periodic(sweep_now, every_s=SWEEP_INTERVAL_S,
                      name='display-alerts',
                      first_run_delay=SWEEP_INTERVAL_S)
    # Hourly digest tick: digest_now() decides internally whether it's
    # actually time to send. Cheap when not, cheap when yes.
    schedule_periodic(digest_now, every_s=3600,
                      name='display-alerts-digest',
                      first_run_delay=600)
    logger.info('alerts: periodic sweep scheduled every %ss', SWEEP_INTERVAL_S)
