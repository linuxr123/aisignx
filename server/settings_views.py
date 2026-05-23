"""

Settings admin views - Phase 2 / Task B6.



Scopes

------

* **Global (system-wide)** — superadmin only, ``domain_id`` IS NULL.

  Security, rate limits, backups, server disk thresholds, audit retention,

  plugin signing, proof of play, jobs, SSE limits, etc.



* **Per-tenant** — tenant admins with ``domain.admin`` + ``settings.read``

  may edit at ``domain_id = N``. Currently: ``alerts.*`` (SMTP, webhooks,

  display-offline thresholds for that tenant) except cross-tenant keys like

  ``alerts.user_recipients``.



* **Superadmin per-tenant** — only superadmin at ``domain_id = N``.

  ``tenant.storage_quota_mb`` (maps to ``Domain.storage_quota_bytes``).



Sensitive values stay superadmin-only regardless of scope.

"""

from flask import Blueprint, render_template, request, jsonify, abort

from flask_login import login_required, current_user



import settings as settings_module

from models import SystemSetting, Domain, db

from tenant_filter import bypass_tenant_filter

from permissions import has_permission

from audit import audit





settings_bp = Blueprint('settings_admin', __name__)





_REDACTED = '***'

_MB = 1024 * 1024





def _is_superadmin():

    return getattr(current_user, 'is_superadmin', False)





def _has_domain_admin(domain_id):

    return has_permission(current_user, 'domain.admin', domain_id=domain_id)





def _can_edit_scope(domain_id):

    """Legacy helper: may write at this scope at all."""

    if domain_id is None:

        return _is_superadmin()

    if _is_superadmin():

        return True

    return _has_domain_admin(domain_id)





def _can_write_setting(key, domain_id):

    """Fine-grained write permission for one key at one scope."""

    if key.startswith('auto.'):

        return False

    if settings_module.is_virtual_domain_key(key):

        return _is_superadmin() and domain_id is not None

    scope = settings_module.setting_scope(key)

    if scope == settings_module.SCOPE_GLOBAL:

        return _is_superadmin() and domain_id is None

    if scope == settings_module.SCOPE_SUPERADMIN_TENANT:

        return _is_superadmin() and domain_id is not None

    if scope == settings_module.SCOPE_TENANT:

        if domain_id is None:

            return _is_superadmin()

        return _is_superadmin() or _has_domain_admin(domain_id)

    return False





def _include_key_in_list(key, is_superadmin, tenant_only_view, scope_domain_id):

    """Filter catalog rows for the current viewer and scope selector."""

    if key.startswith('auto.'):

        return is_superadmin

    scope = settings_module.setting_scope(key)

    if not is_superadmin:

        return tenant_only_view and scope == settings_module.SCOPE_TENANT

    if scope_domain_id is None:

        return scope == settings_module.SCOPE_GLOBAL

    return scope in (settings_module.SCOPE_TENANT,

                     settings_module.SCOPE_SUPERADMIN_TENANT)





def _spec_for(key):

    return settings_module.BUILTIN_DEFAULTS.get(key)





def _domain_quota_mb(domain_id):

    with bypass_tenant_filter():

        d = db.session.get(Domain, domain_id)

    if not d or not d.storage_quota_bytes:

        return None

    return int(round(d.storage_quota_bytes / _MB))





def _set_domain_quota_mb(domain_id, mb_value):

    """Persist quota on Domain (source of truth for storage.check_quota)."""

    with bypass_tenant_filter():

        d = db.session.get(Domain, domain_id)

        if d is None:

            raise ValueError('domain not found')

        if mb_value is None or mb_value == '':

            new_bytes = None

        else:

            mb = int(mb_value)

            if mb < 0:

                raise ValueError('quota must be non-negative')

            new_bytes = mb * _MB if mb > 0 else None

        old_bytes = d.storage_quota_bytes

        d.storage_quota_bytes = new_bytes

        db.session.commit()

    return old_bytes, new_bytes





def _virtual_entry(key, scope_domain_id):

    """Synthetic catalog row for keys not stored in system_setting."""

    if key != 'tenant.storage_quota_mb' or scope_domain_id is None:

        return None

    spec = _spec_for(key)

    default, vtype, sensitive, description = spec

    effective = _domain_quota_mb(scope_domain_id)

    return {

        'key':              key,

        'description':      description,

        'value_type':       vtype,

        'default':          default,

        'is_sensitive':     sensitive,

        'is_builtin':       True,

        'is_auto_key':      False,

        'scope':            settings_module.SCOPE_SUPERADMIN_TENANT,

        'editable':         _can_write_setting(key, scope_domain_id),

        'global_value':     None,

        'global_is_auto':   False,

        'global_updated_at': None,

        'domain_value':     effective,

        'domain_updated_at': None,

        'effective':        effective,

    }





def _row_to_dict(row, redact_sensitive=True):

    spec = _spec_for(row.key)

    sensitive = bool(row.is_sensitive or (spec and spec[2]))

    decoded = settings_module._decode(row.value, row.value_type)

    return {

        'key':           row.key,

        'value':         _REDACTED if (sensitive and redact_sensitive and decoded)

                                   else decoded,

        'value_type':    row.value_type,

        'is_auto':       row.is_auto,

        'is_sensitive':  sensitive,

        'domain_id':     row.domain_id,

        'updated_at':    row.updated_at.isoformat() if row.updated_at else None,

        'updated_by_user_id': row.updated_by_user_id,

    }





@settings_bp.route('/admin/settings')

@login_required

def admin_settings_page():

    if _is_superadmin():

        return render_template('admin_settings.html', tenant_only=False,

                               policy=settings_module.settings_policy_summary())

    from admin import _administrable_domain_ids, _administrable_domains

    if not _administrable_domain_ids():

        abort(403)

    if not has_permission(current_user, 'settings.read'):

        abort(403)

    from domains import domain_switcher_state
    ds = domain_switcher_state()
    return render_template('admin_settings.html', tenant_only=True,

                           administrable_domains=_administrable_domains(),

                           active_domain_id=ds.get('current_id'),

                           policy=settings_module.settings_policy_summary())


@settings_bp.route('/admin/tenant/alerts')

@login_required

def admin_tenant_alerts_page():

    """Per-tenant alert rules and delivery for domain administrators."""

    from admin import _administrable_domains, _can_access_tenant_alerts_ui

    if not _can_access_tenant_alerts_ui():

        abort(403)

    from domains import domain_switcher_state

    ds = domain_switcher_state()

    return render_template('admin_alert_settings.html', tenant_mode=True,

                           active_domain_id=ds.get('current_id'),

                           administrable_domains=_administrable_domains())





@settings_bp.route('/api/settings/policy', methods=['GET'])

@login_required

def api_settings_policy():

    """Document which keys are global vs tenant-editable."""

    if not _is_superadmin():

        from admin import _administrable_domain_ids

        if not _administrable_domain_ids():

            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

        if not has_permission(current_user, 'settings.read'):

            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    return jsonify({

        'status': 'success',

        'policy': settings_module.settings_policy_summary(),

    })





@settings_bp.route('/api/settings', methods=['GET'])

@login_required

def api_list_settings():

    scope_domain_id = request.args.get('domain_id', type=int)

    tenant_only_view = not _is_superadmin()



    if not _is_superadmin():

        from admin import _administrable_domain_ids

        admin_ids = _administrable_domain_ids()

        if not admin_ids:

            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

        if not has_permission(current_user, 'settings.read'):

            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

        if scope_domain_id is None:

            from tenant_filter import current_domain_id

            cur = current_domain_id()

            scope_domain_id = cur if cur in admin_ids else admin_ids[0]

        elif scope_domain_id not in admin_ids:

            return jsonify({'status': 'error', 'message': 'forbidden'}), 403

    elif scope_domain_id is not None and not _can_edit_scope(scope_domain_id):

        return jsonify({'status': 'error', 'message': 'forbidden'}), 403



    domain_name = None

    if scope_domain_id is not None:

        with bypass_tenant_filter():

            d = db.session.get(Domain, scope_domain_id)

            if d is None:

                return jsonify({'status': 'error', 'message': 'domain not found'}), 404

            domain_name = d.name



    with bypass_tenant_filter():

        global_rows = {r.key: r for r in

                       SystemSetting.query.filter_by(domain_id=None).all()}

        domain_rows = {}

        if scope_domain_id is not None:

            domain_rows = {r.key: r for r in

                           SystemSetting.query.filter_by(

                               domain_id=scope_domain_id).all()}



    all_keys = (set(settings_module.BUILTIN_DEFAULTS)

                | set(global_rows)

                | set(domain_rows))



    entries = []

    for key in sorted(all_keys):

        if not _include_key_in_list(key, _is_superadmin(), tenant_only_view,

                                    scope_domain_id):

            continue

        if settings_module.is_virtual_domain_key(key):

            continue



        spec = _spec_for(key)

        is_builtin = spec is not None

        if is_builtin:

            default, vtype, sensitive, description = spec

        else:

            default, sensitive, description = None, False, ''

            ref = domain_rows.get(key) or global_rows.get(key)

            vtype = ref.value_type if ref else 'string'



        gr = global_rows.get(key)

        dr = domain_rows.get(key)



        global_value = settings_module._decode(gr.value, gr.value_type) if gr else None

        domain_value = settings_module._decode(dr.value, dr.value_type) if dr else None

        if dr is not None:

            effective = domain_value

        elif gr is not None:

            effective = global_value

        else:

            effective = default



        if sensitive:

            redacted = _REDACTED

            global_value = redacted if global_value else None

            domain_value = redacted if domain_value else None

            effective    = redacted if effective else None



        scope = settings_module.setting_scope(key)

        entries.append({

            'key':              key,

            'description':      description,

            'value_type':       vtype,

            'default':          default,

            'is_sensitive':     sensitive,

            'is_builtin':       is_builtin,

            'is_auto_key':      key.startswith('auto.'),

            'scope':            scope,

            'editable':         _can_write_setting(key, scope_domain_id),

            'global_value':     global_value,

            'global_is_auto':   bool(gr.is_auto) if gr else False,

            'global_updated_at': gr.updated_at.isoformat() if gr and gr.updated_at else None,

            'domain_value':     domain_value,

            'domain_updated_at': dr.updated_at.isoformat() if dr and dr.updated_at else None,

            'effective':        effective,

        })



    if scope_domain_id is not None and _is_superadmin():

        virt = _virtual_entry('tenant.storage_quota_mb', scope_domain_id)

        if virt:

            entries.append(virt)

            entries.sort(key=lambda e: e['key'])



    return jsonify({

        'status': 'success',

        'scope': {'domain_id': scope_domain_id, 'domain_name': domain_name},

        'policy': settings_module.settings_policy_summary(),

        'entries': entries,

    })





def _parse_value(raw, value_type):

    if raw is None:

        return True, None

    if value_type == 'int':

        try:

            return True, int(raw)

        except (TypeError, ValueError):

            return False, 'value must be an integer'

    if value_type == 'bool':

        if isinstance(raw, bool):

            return True, raw

        if isinstance(raw, str):

            return True, raw.lower() in ('1', 'true', 'yes', 'on')

        return True, bool(raw)

    if value_type == 'json':

        return True, raw

    return True, '' if raw is None else str(raw)





@settings_bp.route('/api/settings/<key>', methods=['PUT'])

@login_required

def api_set_setting(key):

    data = request.get_json(silent=True) or {}

    domain_id = data.get('domain_id')

    if domain_id is not None:

        try:

            domain_id = int(domain_id)

        except (TypeError, ValueError):

            return jsonify({'status': 'error',

                            'message': 'domain_id must be an integer or null'}), 400



    if not _can_write_setting(key, domain_id):

        return jsonify({'status': 'error', 'message': 'forbidden'}), 403



    if key == 'tenant.storage_quota_mb':

        if domain_id is None:

            return jsonify({'status': 'error',

                            'message': 'tenant.storage_quota_mb requires domain_id'}), 400

        raw = data.get('value')

        if raw in ('', None):

            parsed = None

        else:

            ok, parsed = _parse_value(raw, 'int')

            if not ok:

                return jsonify({'status': 'error', 'message': parsed}), 400

        try:

            old_bytes, new_bytes = _set_domain_quota_mb(domain_id, parsed)

        except ValueError as exc:

            return jsonify({'status': 'error', 'message': str(exc)}), 400

        audit('settings.set', target_type='setting', target_id=key,

              payload={'domain_id': domain_id,

                       'from_bytes': old_bytes, 'to_bytes': new_bytes},

              domain_id=domain_id)

        eff = _domain_quota_mb(domain_id)

        return jsonify({'status': 'success', 'key': key, 'effective': eff})



    spec = _spec_for(key)

    if spec is None and not key.startswith('auto.'):

        if not data.get('allow_unknown'):

            return jsonify({'status': 'error',

                            'message': (f'Unknown setting key {key!r}. Pass '

                                        'allow_unknown=true to confirm.')}), 400



    if key.startswith('auto.'):

        return jsonify({'status': 'error',

                        'message': 'auto.* keys are server-managed and read-only here'}), 400



    if spec is not None and spec[2] and not _is_superadmin():

        return jsonify({'status': 'error',

                        'message': 'sensitive keys are superadmin-only'}), 403



    raw = data.get('value')

    value_type = (spec[1] if spec else (data.get('value_type') or 'string'))



    ok, parsed = _parse_value(raw, value_type)

    if not ok:

        return jsonify({'status': 'error', 'message': parsed}), 400



    old = settings_module.get(key, domain_id=domain_id, default=None)



    settings_module.set(key, parsed, domain_id=domain_id,

                        user_id=current_user.id,

                        is_auto=False,

                        value_type=value_type,

                        _allow_unknown=(spec is None))



    audit('settings.set', target_type='setting', target_id=key,

          payload={'domain_id': domain_id,

                   'from': (_REDACTED if (spec and spec[2]) else old),

                   'to':   (_REDACTED if (spec and spec[2]) else parsed)},

          domain_id=domain_id)



    new_eff = settings_module.effective_value(key, domain_id=domain_id)

    return jsonify({

        'status':    'success',

        'key':       key,

        'effective': (_REDACTED if (spec and spec[2] and new_eff) else new_eff),

    })





@settings_bp.route('/api/settings/<key>', methods=['DELETE'])

@login_required

def api_delete_setting(key):

    domain_id = request.args.get('domain_id', type=int)



    if not _can_write_setting(key, domain_id):

        return jsonify({'status': 'error', 'message': 'forbidden'}), 403



    if key == 'tenant.storage_quota_mb':

        if domain_id is None:

            return jsonify({'status': 'error', 'message': 'domain_id required'}), 400

        try:

            old_bytes, new_bytes = _set_domain_quota_mb(domain_id, None)

        except ValueError as exc:

            return jsonify({'status': 'error', 'message': str(exc)}), 400

        audit('settings.delete', target_type='setting', target_id=key,

              payload={'domain_id': domain_id, 'from_bytes': old_bytes},

              domain_id=domain_id)

        return jsonify({'status': 'success', 'key': key, 'effective': None})



    spec = _spec_for(key)

    if spec is not None and spec[2] and not _is_superadmin():

        return jsonify({'status': 'error',

                        'message': 'sensitive keys are superadmin-only'}), 403

    if key.startswith('auto.'):

        return jsonify({'status': 'error',

                        'message': 'auto.* keys are server-managed; delete refused'}), 400



    old = settings_module.get(key, domain_id=domain_id, default=None)

    settings_module.delete(key, domain_id=domain_id)



    audit('settings.delete', target_type='setting', target_id=key,

          payload={'domain_id': domain_id,

                   'value': (_REDACTED if (spec and spec[2]) else old)},

          domain_id=domain_id)



    new_eff = settings_module.effective_value(key, domain_id=domain_id)

    return jsonify({

        'status':    'success',

        'key':       key,

        'effective': (_REDACTED if (spec and spec[2] and new_eff) else new_eff),

    })

