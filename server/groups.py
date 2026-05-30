from flask import Blueprint, jsonify, request, render_template
from flask_login import login_required
from models import DisplayGroup, Display, db
from utils import api_auth_required
from permissions import require_permission
from audit import audit

groups_bp = Blueprint('groups', __name__)


# ----------------------------------------------------------------------------
# Hierarchy helpers
#
# Groups form a forest within each tenant: parent_id is nullable
# (root-level groups), self-referential, and ondelete=SET NULL so deleting
# a parent reparents its children to the root rather than cascading them
# into oblivion.
#
# Cycle prevention: walk parent chain on every parent-set; refuse if we'd
# end up pointing at ourselves or a descendant. Trees are small (typical
# install: dozens of groups, never thousands) so the O(depth) walk is
# negligible.
# ----------------------------------------------------------------------------

def _would_create_cycle(group_id: int, new_parent_id: int) -> bool:
    """Return True iff setting group_id.parent_id = new_parent_id would
    introduce a cycle. A node cannot be its own parent, nor an ancestor
    of its proposed parent."""
    if new_parent_id is None:
        return False
    if new_parent_id == group_id:
        return True
    # Walk up from new_parent and see if we hit group_id.
    seen = set()
    cur = DisplayGroup.query.get(new_parent_id)
    while cur is not None and cur.id not in seen:
        if cur.id == group_id:
            return True
        seen.add(cur.id)
        if cur.parent_id is None:
            return False
        cur = DisplayGroup.query.get(cur.parent_id)
    return False


def _validate_parent(parent_id, child_domain_id, child_id=None):
    """Return (parent_obj_or_None, error_message_or_None). child_id may
    be None for new groups (no cycle check needed in that case)."""
    if parent_id is None:
        return None, None
    parent = DisplayGroup.query.get(parent_id)
    if parent is None:
        return None, f'parent group id={parent_id} not found'
    # The tenant filter usually excludes other tenants' rows already, but
    # a defense-in-depth check protects against bypass-context callers.
    if parent.domain_id != child_domain_id:
        return None, 'parent group must be in the same domain'
    if child_id is not None and _would_create_cycle(child_id, parent_id):
        return None, 'parent assignment would create a cycle'
    return parent, None


def _descendant_ids(group_id: int) -> set:
    """All group ids descended from `group_id`, NOT including itself.
    Iterative BFS using the children backref."""
    out = set()
    stack = [group_id]
    while stack:
        cur = stack.pop()
        kids = DisplayGroup.query.filter_by(parent_id=cur).all()
        for k in kids:
            if k.id not in out:
                out.add(k.id)
                stack.append(k.id)
    return out


def _ancestor_ids(group_id: int) -> list:
    """All group ids upward from `group_id` to the root, NOT including
    itself. Returned in walk order: nearest parent first.

    Used by the schedule resolver: a schedule attached to a parent
    group propagates to every display in any descendant group. We walk
    *up* from the display's group to find every ancestor whose
    schedules should also apply.

    Cycle protection (shouldn't happen given the API's cycle check, but
    cheap and correct): bail if we revisit an id.
    """
    out = []
    seen = set()
    cur = DisplayGroup.query.get(group_id)
    if cur is None:
        return out
    while cur.parent_id is not None and cur.parent_id not in seen:
        seen.add(cur.parent_id)
        out.append(cur.parent_id)
        cur = DisplayGroup.query.get(cur.parent_id)
        if cur is None:
            break
    return out


def resolve_effective_group_ids(display) -> list:
    """Return the list of group ids whose schedules apply to a display:
        [display.group_id, *all ancestors up to the root]
    Returns [] if the display isn't in any group.

    This is the foundation of schedule inheritance through the group
    hierarchy: assigning a schedule to a parent group transparently
    covers every display in every descendant group.
    """
    if not getattr(display, 'group_id', None):
        return []
    return [display.group_id] + _ancestor_ids(display.group_id)


@groups_bp.route('/groups')
@login_required
@require_permission('group.read')
def groups():
    """Display groups management page"""
    groups = DisplayGroup.query.all()
    children = {}
    by_id = {g.id: g for g in groups}
    roots = []
    for group in groups:
        if group.parent_id and group.parent_id in by_id:
            children.setdefault(group.parent_id, []).append(group)
        else:
            roots.append(group)
    for rows in children.values():
        rows.sort(key=lambda g: (g.name or '').lower())
    roots.sort(key=lambda g: (g.name or '').lower())

    grouped = []

    def append_branch(group, depth=0):
        setattr(group, '_grid_depth', depth)
        grouped.append(group)
        for child in children.get(group.id, []):
            append_branch(child, depth + 1)

    for root in roots:
        append_branch(root, 0)
    groups = grouped
    return render_template('groups.html', groups=groups)


@groups_bp.route('/api/groups', methods=['GET'])
@api_auth_required(['group:read'])
@require_permission('group.read')
def api_get_groups():
    """List groups. ?tree=true returns a nested structure keyed off
    root groups; default returns the flat list (back-compat)."""
    groups = DisplayGroup.query.all()
    flat = [g.to_dict() for g in groups]

    if request.args.get('tree', '').lower() not in ('1', 'true', 'yes'):
        return jsonify({'status': 'success', 'groups': flat})

    # Build tree. By-id index, then attach each non-root to its parent's
    # children list. Orphans (parent_id pointing to a deleted group --
    # shouldn't happen with SET NULL but defensive anyway) become roots.
    by_id = {d['id']: dict(d, children=[]) for d in flat}
    roots = []
    for d in by_id.values():
        pid = d.get('parent_id')
        if pid is not None and pid in by_id:
            by_id[pid]['children'].append(d)
        else:
            roots.append(d)
    # Stable sort by name at every level for predictable rendering.
    def _sort(node):
        node['children'].sort(key=lambda x: (x['name'] or '').lower())
        for c in node['children']:
            _sort(c)
    roots.sort(key=lambda x: (x['name'] or '').lower())
    for r in roots:
        _sort(r)
    return jsonify({'status': 'success', 'groups': roots, 'tree': True})


@groups_bp.route('/api/groups', methods=['POST'])
@api_auth_required(['group:write'])
@require_permission('group.edit')
def api_create_group():
    data = request.json or {}
    if not data.get('name'):
        return jsonify({'status': 'error',
                        'message': 'Group name is required'}), 400

    # Resolve target domain. The tenant filter scopes the insert anyway,
    # but we need the domain id explicitly to validate parent_id.
    from tenant_filter import current_domain_id
    did = current_domain_id()

    parent_id = data.get('parent_id')
    parent, err = _validate_parent(parent_id, did, child_id=None)
    if err:
        return jsonify({'status': 'error', 'message': err}), 400

    group = DisplayGroup(
        name=data['name'],
        description=data.get('description', ''),
        parent_id=parent.id if parent else None,
    )
    db.session.add(group)
    db.session.commit()
    audit('group.create', target_type='group', target_id=str(group.id),
          payload={'name': group.name, 'parent_id': group.parent_id})
    return jsonify({
        'status': 'success',
        'message': 'Group created successfully',
        'group': group.to_dict()
    })


@groups_bp.route('/api/groups/<int:group_id>', methods=['GET'])
@api_auth_required(['group:read'])
@require_permission('group.read')
def api_get_group(group_id):
    group = DisplayGroup.query.get_or_404(group_id)
    return jsonify({'status': 'success', 'group': group.to_dict()})


@groups_bp.route('/api/groups/<int:group_id>', methods=['PUT'])
@api_auth_required(['group:write'])
@require_permission('group.edit')
def api_update_group(group_id):
    group = DisplayGroup.query.get_or_404(group_id)
    data = request.json or {}
    changes = {}

    if data.get('name') and data['name'] != group.name:
        changes['name'] = (group.name, data['name'])
        group.name = data['name']

    if 'description' in data and data['description'] != group.description:
        changes['description'] = (group.description, data['description'])
        group.description = data['description']

    if 'sync_playback' in data:
        new_sync = bool(data['sync_playback'])
        if new_sync != bool(group.sync_playback):
            changes['sync_playback'] = (bool(group.sync_playback), new_sync)
            group.sync_playback = new_sync

    if 'parent_id' in data:
        new_parent = data['parent_id']
        # Allow null to detach.
        if new_parent != group.parent_id:
            parent, err = _validate_parent(new_parent, group.domain_id,
                                            child_id=group.id)
            if err:
                return jsonify({'status': 'error', 'message': err}), 400
            changes['parent_id'] = (group.parent_id,
                                    parent.id if parent else None)
            group.parent_id = parent.id if parent else None

    db.session.commit()
    if changes:
        audit('group.update', target_type='group', target_id=str(group.id),
              payload={'changes': {k: {'from': v[0], 'to': v[1]}
                                   for k, v in changes.items()}})
    return jsonify({
        'status': 'success',
        'message': 'Group updated successfully',
        'group': group.to_dict()
    })


@groups_bp.route('/api/groups/bulk-update', methods=['POST'])
@api_auth_required(['group:write'])
@require_permission('group.edit')
def api_bulk_update_groups():
    """Apply the same editable group fields to many groups.

    Body: {"ids": [int,...], "changes": {"parent_id": int|null,
                                         "sync_playback": bool}}
    Name/description are intentionally excluded because they are per-row
    values, not safe fleet-level toggles.
    """
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    changes = data.get('changes') or {}
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400
    if not isinstance(changes, dict) or not changes:
        return jsonify({'status': 'error', 'message': 'changes is empty'}), 400

    allowed = {}
    if 'sync_playback' in changes:
        allowed['sync_playback'] = bool(changes.get('sync_playback'))
    if 'parent_id' in changes:
        raw = changes.get('parent_id')
        if raw in ('', None):
            allowed['parent_id'] = None
        else:
            try:
                allowed['parent_id'] = int(raw)
            except (TypeError, ValueError):
                return jsonify({'status': 'error',
                                'message': 'parent_id must be int or null'}), 400
    if not allowed:
        return jsonify({'status': 'error',
                        'message': 'no recognized fields in changes'}), 400

    rows = DisplayGroup.query.filter(DisplayGroup.id.in_(ids)).all()
    found_ids = {g.id for g in rows}
    updated = 0
    per_row = []
    for g in rows:
        row_changes = {}
        if 'parent_id' in allowed:
            new_parent = allowed['parent_id']
            if new_parent == g.id:
                per_row.append({'id': g.id, 'ok': False, 'error': 'cannot parent to self'})
                continue
            parent, err = _validate_parent(new_parent, g.domain_id, child_id=g.id)
            if err:
                per_row.append({'id': g.id, 'ok': False, 'error': err})
                continue
            if g.parent_id != (parent.id if parent else None):
                row_changes['parent_id'] = {'from': g.parent_id,
                                            'to': parent.id if parent else None}
                g.parent_id = parent.id if parent else None
        if 'sync_playback' in allowed:
            new_sync = allowed['sync_playback']
            if bool(g.sync_playback) != new_sync:
                row_changes['sync_playback'] = {'from': bool(g.sync_playback),
                                                'to': new_sync}
                g.sync_playback = new_sync
        if row_changes:
            updated += 1
        per_row.append({'id': g.id, 'ok': True, 'changes': row_changes})
    db.session.commit()

    not_found = [i for i in ids if i not in found_ids]
    audit('groups.bulk_update', target_type='groups',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'requested': len(ids), 'updated': updated,
                   'not_found': not_found, 'changes': allowed,
                   'results': per_row})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'updated': updated, 'not_found': not_found,
                    'results': per_row})


@groups_bp.route('/api/groups/<int:group_id>', methods=['DELETE'])
@api_auth_required(['group:write'])
@require_permission('group.delete')
def api_delete_group(group_id):
    group = DisplayGroup.query.get_or_404(group_id)
    # ondelete=SET NULL on parent_id reparents children to the root
    # automatically; we don't have to walk descendants.
    snapshot = {'name': group.name,
                'parent_id': group.parent_id,
                'detached_display_ids': [d.id for d in group.displays],
                'reparented_child_ids': [c.id for c in group.children.all()]}
    for display in group.displays:
        display.group_id = None
    db.session.delete(group)
    db.session.commit()
    audit('group.delete', target_type='group', target_id=str(group_id),
          payload=snapshot)
    return jsonify({'status': 'success',
                    'message': 'Group deleted successfully'})


@groups_bp.route('/api/groups/<int:group_id>/displays', methods=['GET'])
@api_auth_required(['group:read'])
@require_permission('group.read')
def api_get_group_displays(group_id):
    """List displays in a group. ?recursive=true also includes displays
    in all descendant groups -- the "effective members" that future
    schedule/playlist propagation will target."""
    group = DisplayGroup.query.get_or_404(group_id)
    recursive = request.args.get('recursive', '').lower() in ('1', 'true', 'yes')

    if not recursive:
        return jsonify({
            'status': 'success',
            'displays': [d.to_dict() for d in group.displays]
        })

    ids = _descendant_ids(group.id)
    ids.add(group.id)
    rows = Display.query.filter(Display.group_id.in_(ids)).all()
    return jsonify({
        'status': 'success',
        'displays': [d.to_dict() for d in rows],
        'recursive': True,
        'group_ids_included': sorted(ids),
    })


@groups_bp.route('/api/groups/<int:group_id>/command', methods=['POST'])
@api_auth_required(['display:write'])
@require_permission('display.control')
def api_group_command(group_id):
    """Push a one-off command (reboot/update/reload) to every display in
    a group. By default also recurses into descendant groups -- the same
    "effective members" set used for schedule inheritance.

    Body: {"action": "reload"|"reboot"|"update",
           "recursive": true|false (default true),
           "payload": {...optional}}

    Returns per-display delivery status so the operator can see which
    displays are offline and missed the push.
    """
    group = DisplayGroup.query.get_or_404(group_id)
    data = request.get_json(silent=True) or {}
    action = (data.get('action') or '').strip().lower()
    valid = {'reboot', 'update', 'reload', 'release_device_owner'}
    if action not in valid:
        return jsonify({'status': 'error',
                        'message': f'action must be one of: {", ".join(sorted(valid))}'}), 400

    recursive = data.get('recursive', True)
    if recursive in ('false', '0', 'no', False):
        recursive = False

    if recursive:
        ids = _descendant_ids(group.id)
        ids.add(group.id)
        targets = Display.query.filter(Display.group_id.in_(ids)).all()
    else:
        targets = list(group.displays)

    from display_player import push_command
    payload = data.get('payload')
    results = []
    delivered_count = 0
    for d in targets:
        ok = push_command(d.api_key, action, payload)
        if ok:
            delivered_count += 1
        results.append({'id': d.id, 'name': d.name, 'delivered': ok})

    audit('group.command', target_type='group', target_id=str(group.id),
          payload={'action': action, 'recursive': bool(recursive),
                   'targets': len(targets), 'delivered': delivered_count})

    return jsonify({
        'status':    'success',
        'action':    action,
        'recursive': bool(recursive),
        'targets':   len(targets),
        'delivered': delivered_count,
        'results':   results,
    })


@groups_bp.route('/api/groups/<int:group_id>/snooze-alerts', methods=['POST'])
@require_permission('display.control')
def api_group_snooze_alerts(group_id):
    """Snooze offline alerts for every display in a group.

    Body: {"hours": <float>,           -- 0 to clear
           "reason": "<text>",
           "recursive": true|false (default true)}

    Mirrors api_group_command's targeting so "snooze the whole store"
    matches what "reload the whole store" would hit.
    """
    group = DisplayGroup.query.get_or_404(group_id)
    data = request.get_json(silent=True) or {}
    try:
        hours = float(data.get('hours', 0))
    except (TypeError, ValueError):
        return jsonify({'status': 'error', 'message': 'hours must be numeric'}), 400
    reason = (data.get('reason') or '').strip()[:200]

    recursive = data.get('recursive', True)
    if recursive in ('false', '0', 'no', False):
        recursive = False

    if recursive:
        ids = _descendant_ids(group.id)
        ids.add(group.id)
        targets = Display.query.filter(Display.group_id.in_(ids)).all()
    else:
        targets = list(group.displays)

    import alerts as _alerts
    results = []
    for d in targets:
        try:
            r = _alerts.snooze_display(d.id, hours, reason=reason)
            results.append({'id': d.id, 'name': d.name,
                            'snoozed_until': r.get('snoozed_until')})
        except Exception as exc:
            results.append({'id': d.id, 'name': d.name, 'error': str(exc)})

    audit('group.alerts_snoozed', target_type='group', target_id=str(group.id),
          payload={'hours': hours, 'reason': reason,
                   'recursive': bool(recursive), 'targets': len(targets)})

    return jsonify({
        'status':    'success',
        'group_id':  group.id,
        'recursive': bool(recursive),
        'targets':   len(targets),
        'hours':     hours,
        'results':   results,
    })
