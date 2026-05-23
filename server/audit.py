"""
Audit logging - Phase 1, Task 4.

Single helper `audit(action, ...)` writes one AuditLog row with the current
request context. Sensitive fields are auto-redacted from the payload by
substring match against SENSITIVE_KEYS (deny-list). Writes are best-effort
and never raise -- a logging failure must not break a request.

Usage from a route handler:

    from audit import audit
    audit('media.delete', target_type='media', target_id=str(m.id),
          payload={'name': m.name})
"""
import re
from copy import deepcopy

from flask import request, has_request_context, g
from flask_login import current_user

from logging_config import logger
from models import db, AuditLog
from tenant_filter import current_domain_id, bypass_tenant_filter


SENSITIVE_KEYS = (
    'password', 'passwd', 'secret', 'api_key', 'apikey', 'token',
    'unlock_pin', 'pin', 'authorization', 'cookie', 'session',
    'oauth_client_secret', 'signing_key',
)


def _redact(obj):
    """Recursively redact suspicious keys in a dict/list/scalar payload."""
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            kl = str(k).lower()
            if any(sk in kl for sk in SENSITIVE_KEYS):
                out[k] = '***'
            else:
                out[k] = _redact(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [_redact(x) for x in obj]
    if isinstance(obj, str) and len(obj) > 4096:
        return obj[:4096] + '...[truncated]'
    return obj


def audit(action, target_type=None, target_id=None, payload=None,
          domain_id=None, user_id=None):
    """Write a single audit log row. Never raises.

    action       (str)  required  - dot-form key, e.g. 'media.delete'
    target_type  (str)  optional  - 'media' | 'display' | 'playlist' | ...
    target_id    (str)  optional  - id of the target (str so cross-table refs work)
    payload      (dict) optional  - extra context (will be redacted)
    domain_id    (int)  optional  - override; defaults to current_domain_id()
    user_id      (int)  optional  - override; defaults to current_user.id
    """
    try:
        ip = ua = None
        api_token_id = None
        if has_request_context():
            ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            if ip and ',' in ip:
                ip = ip.split(',', 1)[0].strip()
            ua = (request.user_agent.string or '')[:255] if request.user_agent else None
            tok = getattr(g, 'api_token', None)
            if tok is not None:
                api_token_id = tok.id

        if user_id is None and has_request_context():
            try:
                api_user = getattr(g, 'api_user', None)
                if api_user is not None and getattr(api_user, 'is_authenticated', False):
                    user_id = api_user.id
                elif current_user.is_authenticated:
                    user_id = current_user.id
            except Exception:
                pass

        if domain_id is None:
            domain_id = current_domain_id()

        clean_payload = _redact(deepcopy(payload)) if payload is not None else None

        with bypass_tenant_filter():
            row = AuditLog(
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id is not None else None,
                payload=clean_payload,
                domain_id=domain_id,
                actor_user_id=user_id,
                actor_api_token_id=api_token_id,
                ip_address=ip,
                user_agent=ua,
            )
            db.session.add(row)
            db.session.commit()
    except Exception as e:
        try:
            db.session.rollback()
        except Exception:
            pass
        # Never fail a request because of audit logging.
        logger.warning(f'audit({action!r}) failed: {e}')
