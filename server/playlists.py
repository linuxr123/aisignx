import os
from flask import Blueprint, render_template, request, jsonify, url_for, flash, current_app, g
from flask_login import login_required, current_user
from sqlalchemy.sql import func
from datetime import datetime
from models import db, Playlist, PlaylistItem, Media, Display, Schedule
from logging_config import logger
from plugin_system import build_plugin_url, get_plugin_meta
from utils import compute_playlist_version, api_auth_required
from permissions import require_permission
from audit import audit
from displays import _real_client_ip, _check_single_client
from media_duration import detected_media_duration, playlist_item_duration_seconds
import storage

playlists_bp = Blueprint('playlists', __name__)

# Per-item playback tweaks accept a small whitelist of values. Anything
# unknown silently falls back to the default so a malicious or stale
# client cannot inject arbitrary CSS classes through the player.
# Per-item transitions: 'none' is accepted as an alias for 'cut'.
_ALLOWED_TRANSITIONS = {
    'cut', 'fade', 'crossfade', 'wipe',
    'slide-left', 'slide-right', 'slide-up', 'slide-down',
    'zoom', 'spin', 'flip', 'iris', 'puzzle',
}
# Playlist-level default also accepts 'random' (player picks per slide).
_ALLOWED_DEFAULT_TRANSITIONS = _ALLOWED_TRANSITIONS | {'random'}
_ALLOWED_ASPECTS     = {'fit', 'fill', 'stretch', 'center'}

def _clean_transition(val):
    s = (val or 'cut').strip().lower()
    if s == 'none':
        return 'cut'
    return s if s in _ALLOWED_TRANSITIONS else 'cut'

def _clean_default_transition(val):
    """Validator for Playlist.default_transition. Accepts the same values
    as a per-item transition plus 'random'. 'none' is an alias for 'cut'."""
    if val is None:
        return 'cut'
    s = str(val).strip().lower()
    if s == 'none':
        return 'cut'
    return s if s in _ALLOWED_DEFAULT_TRANSITIONS else 'cut'

def _clean_aspect(val):
    if val is None or val == '':
        return None
    s = str(val).strip().lower()
    return s if s in _ALLOWED_ASPECTS else None


def _item_duration_from_payload(data, media, fallback=10):
    """Playlist item duration semantics.

    For videos, omitted duration defaults to detected media length. Explicit
    duration=0 means play full detected video length, or clip_start -> clip_end.
    """
    if not media:
        return int(data.get('duration', fallback))
    if 'duration' in data:
        try:
            return max(0, int(data.get('duration') or 0))
        except (TypeError, ValueError):
            return 0 if media.media_type == 'video' else int(fallback)
    if media.media_type == 'video':
        return detected_media_duration(media, default=fallback)
    return int(getattr(media, 'duration', None) or fallback)


def _apply_smart_fields(playlist, data):
    """Read the optional ``smart_rules`` / ``smart_order`` / ``smart_limit``
    keys from a request body and stamp them onto a Playlist row. Passing
    ``smart_rules: null`` (or an empty dict) converts a smart playlist back
    to a manual one. Unknown keys are ignored so the same payload can carry
    name/description updates too."""
    import json as _json
    from smart_playlists import (
        _normalise_rules, ALLOWED_ORDERS, DEFAULT_LIMIT, MAX_LIMIT,
    )

    if 'smart_rules' in data:
        raw = data.get('smart_rules')
        if raw in (None, '', {}):
            playlist.smart_rules = None
            playlist.smart_order = None
            playlist.smart_limit = None
            return
        if isinstance(raw, str):
            try:
                raw = _json.loads(raw)
            except Exception:
                raw = None
        rules = _normalise_rules(raw)
        if rules is None:
            playlist.smart_rules = None
        else:
            playlist.smart_rules = _json.dumps(rules)

    if 'smart_order' in data:
        v = (data.get('smart_order') or '').strip().lower()
        playlist.smart_order = v if v in ALLOWED_ORDERS else None

    if 'smart_limit' in data:
        try:
            n = int(data.get('smart_limit'))
        except (TypeError, ValueError):
            n = DEFAULT_LIMIT
        if n < 1:
            n = DEFAULT_LIMIT
        if n > MAX_LIMIT:
            n = MAX_LIMIT
        playlist.smart_limit = n

@playlists_bp.route('/playlists')
@login_required
@require_permission('playlist.read')
def playlists():
    counts = dict(
        db.session.query(PlaylistItem.playlist_id, func.count(PlaylistItem.id))
        .group_by(PlaylistItem.playlist_id)
        .all()
    )
    playlists = Playlist.query.order_by(Playlist.name.asc()).all()
    for p in playlists:
        p.item_count = int(counts.get(p.id, 0))
    return render_template('playlists.html', playlists=playlists)

@playlists_bp.route('/playlists/<int:playlist_id>')
@login_required
@require_permission('playlist.read')
def playlist_detail(playlist_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    items = PlaylistItem.query.filter_by(playlist_id=playlist_id).order_by(PlaylistItem.position).all()
    plugin_meta = {}
    for item in items:
        if item.plugin_type:
            plugin_meta[item.id] = get_plugin_meta(item.plugin_type) or {}
    return render_template('playlist_detail.html',
                           playlist=playlist,
                           items=items,
                           plugin_meta=plugin_meta)

# API endpoints

@playlists_bp.route('/api/playlists', methods=['GET'])
@api_auth_required(['playlist:read'])
@require_permission('playlist.read')
def api_get_playlists():
    playlists = Playlist.query.all()
    return jsonify({
        'status': 'success',
        'playlists': [p.to_dict() for p in playlists]
    })

@playlists_bp.route('/api/playlists', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_create_playlist():
    try:
        data = request.json
        logger.info(f"Creating playlist with data: {data}")

        if not data or not data.get('name'):
            return jsonify({
                'status': 'error',
                'message': 'Playlist name is required'
            }), 400

        playlist = Playlist(
            name=data.get('name'),
            description=data.get('description', ''),
            default_transition=_clean_default_transition(data.get('default_transition')),
        )
        _apply_smart_fields(playlist, data)

        db.session.add(playlist)
        db.session.commit()
        logger.info(f"Successfully created playlist ID: {playlist.id}")
        audit('playlist.create', target_type='playlist', target_id=str(playlist.id),
              payload={'name': playlist.name})

        return jsonify({
            'status': 'success',
            'message': 'Playlist created successfully',
            'playlist': playlist.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error creating playlist: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error creating playlist: {str(e)}'
        }), 500

@playlists_bp.route('/api/playlists/<int:playlist_id>', methods=['PUT'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_update_playlist(playlist_id):
    """
    Update playlist name and/or description.
    """
    try:
        playlist = Playlist.query.get_or_404(playlist_id)
        data = request.json
        
        if not data:
            return jsonify({
                'status': 'error',
                'message': 'No data provided'
            }), 400
        
        # Update name if provided
        if 'name' in data:
            if not data['name']:
                return jsonify({
                    'status': 'error',
                    'message': 'Playlist name cannot be empty'
                }), 400
            playlist.name = data['name']
        
        # Update description if provided
        if 'description' in data:
            playlist.description = data['description']

        # Update default transition if provided
        if 'default_transition' in data:
            playlist.default_transition = _clean_default_transition(
                data.get('default_transition'))

        # Playlist-wide video audio default: 'inherit' | 'on' | 'off'.
        # 'inherit' falls back to the per-media Media.audio_enabled value;
        # 'on'/'off' force every video in the playlist to that state.
        # Per-item PlaylistItem.mute_audio still wins when set.
        if 'video_audio_default' in data:
            v = (data.get('video_audio_default') or 'inherit').strip().lower()
            if v not in ('inherit', 'on', 'off'):
                v = 'inherit'
            playlist.video_audio_default = v

        # Random transition pool: list of allowed names. Anything outside
        # the allowed set is silently dropped so a stale/malicious client
        # can't smuggle in arbitrary CSS class fragments. Empty list means
        # "use the built-in default pool" (every animated transition).
        if 'random_transitions' in data:
            raw = data.get('random_transitions') or []
            if isinstance(raw, str):
                raw = [s.strip() for s in raw.split(',')]
            cleaned = []
            seen = set()
            for s in raw:
                s = (s or '').strip().lower()
                if s and s != 'cut' and s in _ALLOWED_TRANSITIONS and s not in seen:
                    cleaned.append(s)
                    seen.add(s)
            playlist.random_transitions = ','.join(cleaned)

        # Smart playlist rules: pass-through any of the smart_* keys.
        _apply_smart_fields(playlist, data)

        db.session.commit()
        logger.info(f"Successfully updated playlist ID: {playlist.id}")
        
        return jsonify({
            'status': 'success',
            'message': 'Playlist updated successfully',
            'playlist': playlist.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error updating playlist {playlist_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error updating playlist: {str(e)}'
        }), 500


@playlists_bp.route('/api/playlists/bulk-update', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_bulk_update_playlists():
    """Bulk edit safe playlist-wide settings.

    Body: {"ids": [int,...], "changes": {"description": str,
                                         "default_transition": str,
                                         "video_audio_default": str}}
    Name is intentionally excluded because it is per-playlist identity.
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
    if 'description' in changes:
        allowed['description'] = str(changes.get('description') or '')
    if 'default_transition' in changes:
        allowed['default_transition'] = _clean_default_transition(
            changes.get('default_transition'))
    if 'video_audio_default' in changes:
        v = (changes.get('video_audio_default') or 'inherit').strip().lower()
        allowed['video_audio_default'] = v if v in ('inherit', 'on', 'off') else 'inherit'
    if not allowed:
        return jsonify({'status': 'error',
                        'message': 'no recognized fields in changes'}), 400

    rows = Playlist.query.filter(Playlist.id.in_(ids)).all()
    found_ids = {p.id for p in rows}
    updated = 0
    results = []
    for p in rows:
        row_changes = {}
        for k, v in allowed.items():
            if getattr(p, k, None) != v:
                row_changes[k] = {'from': getattr(p, k, None), 'to': v}
                setattr(p, k, v)
        if row_changes:
            updated += 1
        results.append({'id': p.id, 'ok': True, 'changes': row_changes})
    db.session.commit()

    not_found = [i for i in ids if i not in found_ids]
    audit('playlists.bulk_update', target_type='playlists',
          target_id=','.join(str(i) for i in sorted(found_ids)),
          payload={'requested': len(ids), 'updated': updated,
                   'not_found': not_found, 'changes': allowed,
                   'results': results})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'updated': updated, 'not_found': not_found,
                    'results': results})


@playlists_bp.route('/api/playlists/bulk-delete', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.delete')
def api_bulk_delete_playlists():
    """Delete many playlists at once."""
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not all(isinstance(i, int) for i in ids):
        return jsonify({'status': 'error',
                        'message': 'ids must be a list of integers'}), 400
    if not ids:
        return jsonify({'status': 'error', 'message': 'ids is empty'}), 400
    rows = Playlist.query.filter(Playlist.id.in_(ids)).all()
    found_ids = {p.id for p in rows}
    snapshots = [{'id': p.id, 'name': p.name} for p in rows]
    deleted = len(rows)
    for p in rows:
        db.session.delete(p)
    db.session.commit()
    not_found = [i for i in ids if i not in found_ids]
    audit('playlists.bulk_delete', target_type='playlists',
          target_id=','.join(str(i['id']) for i in snapshots),
          payload={'requested': len(ids), 'deleted': deleted,
                   'not_found': not_found, 'playlists': snapshots})
    return jsonify({'status': 'success', 'requested': len(ids),
                    'deleted': deleted, 'not_found': not_found})

@playlists_bp.route('/api/playlists/<int:playlist_id>/reorder', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_reorder_playlist(playlist_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    data = request.json

    if not data or 'items' not in data:
        return jsonify({
            'status': 'error',
            'message': 'No item data provided'
        }), 400

    try:
        for item_data in data['items']:
            item = PlaylistItem.query.get(item_data['id'])
            if item and item.playlist_id == playlist_id:
                item.position = item_data['position']

        db.session.commit()

        return jsonify({
            'status': 'success',
            'message': 'Playlist order updated successfully'
        })
    except Exception as e:
        db.session.rollback()
        return jsonify({
            'status': 'error',
            'message': f'Error updating playlist order: {str(e)}'
        }), 500

@playlists_bp.route('/api/playlists/<int:playlist_id>', methods=['GET'])
@api_auth_required(['playlist:read'])
@require_permission('playlist.read')
def api_get_playlist(playlist_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    items = PlaylistItem.query.filter_by(playlist_id=playlist_id).order_by(PlaylistItem.position).all()
    return jsonify({
        'status': 'success',
        'playlist': playlist.to_dict(),
        'items': [item.to_dict() for item in items]
    })


@playlists_bp.route('/api/playlists/<int:playlist_id>/smart-preview', methods=['POST'])
@api_auth_required(['playlist:read'])
@require_permission('playlist.read')
def api_smart_preview(playlist_id):
    """Preview the matched media for a smart playlist WITHOUT persisting.
    Body may include override ``smart_rules`` / ``smart_order`` / ``smart_limit``
    so the editor can show "what would this rule match?" before saving."""
    playlist = Playlist.query.get_or_404(playlist_id)
    data = request.get_json(silent=True) or {}

    # Apply overrides on a transient copy so we don't mutate the saved row.
    try:
        if data:
            _apply_smart_fields(playlist, data)
        from smart_playlists import preview as _preview
        media = _preview(playlist)
        return jsonify({
            'status':  'success',
            'count':   len(media),
            'matches': [m.to_dict() for m in media],
        })
    finally:
        # Discard the transient smart_* mutations -- this is read-only.
        db.session.rollback()

@playlists_bp.route('/api/playlists/<int:playlist_id>/items/<int:item_id>', methods=['PUT'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_update_playlist_item(playlist_id, item_id):
    item = PlaylistItem.query.filter_by(id=item_id, playlist_id=playlist_id).first_or_404()
    data = request.json

    if 'duration' in data:
        try:
            item.duration = max(0, int(data.get('duration') or 0))
        except (TypeError, ValueError):
            item.duration = 0 if item.media and item.media.media_type == 'video' else 10

    if 'clip_start' in data:
        v = data.get('clip_start')
        item.clip_start = float(v) if v not in (None, '') else None

    if 'clip_end' in data:
        v = data.get('clip_end')
        item.clip_end = float(v) if v not in (None, '') else None

    if 'position' in data:
        new_position = data.get('position')
        current_position = item.position

        if new_position != current_position:
            if new_position > current_position:
                items_to_update = PlaylistItem.query.filter(
                    PlaylistItem.playlist_id == playlist_id,
                    PlaylistItem.position > current_position,
                    PlaylistItem.position <= new_position
                ).all()

                for update_item in items_to_update:
                    update_item.position -= 1

            elif new_position < current_position:
                items_to_update = PlaylistItem.query.filter(
                    PlaylistItem.playlist_id == playlist_id,
                    PlaylistItem.position >= new_position,
                    PlaylistItem.position < current_position
                ).all()

                for update_item in items_to_update:
                    update_item.position += 1

            item.position = new_position

    if 'plugin_config' in data and item.plugin_type:
        item.plugin_config = data.get('plugin_config')

    if 'transition' in data:
        item.transition = _clean_transition(data.get('transition'))

    if 'aspect_mode' in data:
        item.aspect_mode = _clean_aspect(data.get('aspect_mode'))

    if 'mute_audio' in data:
        item.mute_audio = bool(data.get('mute_audio'))

    db.session.commit()

    return jsonify({
        'status': 'success',
        'message': 'Item updated',
        'item': item.to_dict()
    })

@playlists_bp.route('/api/playlists/<int:playlist_id>', methods=['DELETE'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.delete')
def api_delete_playlist(playlist_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    snapshot = {'name': playlist.name,
                'item_count': PlaylistItem.query.filter_by(playlist_id=playlist.id).count()}
    db.session.delete(playlist)
    db.session.commit()
    audit('playlist.delete', target_type='playlist', target_id=str(playlist_id),
          payload=snapshot)
    return jsonify({
        'status': 'success',
        'message': 'Playlist deleted successfully'
    })

@playlists_bp.route('/api/playlists/<int:playlist_id>/copy', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_copy_playlist(playlist_id):
    """
    Copy a playlist with all its items to a new playlist.
    Accepts optional 'name' in the request JSON to name the new playlist.
    If no name provided, uses "Copy of <original name>".
    """
    try:
        # Get the original playlist
        original_playlist = Playlist.query.get_or_404(playlist_id)
        
        # Get the new name from request or generate one
        data = request.json or {}
        new_name = data.get('name', f"Copy of {original_playlist.name}")
        
        # Create the new playlist
        new_playlist = Playlist(
            name=new_name,
            description=original_playlist.description
        )
        
        db.session.add(new_playlist)
        db.session.flush()  # Get the new playlist ID before adding items
        
        # Copy all playlist items
        original_items = PlaylistItem.query.filter_by(
            playlist_id=playlist_id
        ).order_by(PlaylistItem.position).all()
        
        for original_item in original_items:
            new_item = PlaylistItem(
                playlist_id=new_playlist.id,
                media_id=original_item.media_id,
                position=original_item.position,
                duration=original_item.duration,
                aspect_mode=original_item.aspect_mode,
                plugin_type=original_item.plugin_type,
                plugin_config=original_item.plugin_config
            )
            db.session.add(new_item)
        
        db.session.commit()
        logger.info(f"Successfully copied playlist ID {playlist_id} to new playlist ID {new_playlist.id} with {len(original_items)} items")
        
        return jsonify({
            'status': 'success',
            'message': f'Playlist copied successfully with {len(original_items)} items',
            'playlist': new_playlist.to_dict()
        })
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error copying playlist {playlist_id}: {str(e)}")
        return jsonify({
            'status': 'error',
            'message': f'Error copying playlist: {str(e)}'
        }), 500

@playlists_bp.route('/api/playlists/<int:playlist_id>/items', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_add_playlist_item(playlist_id):
    playlist = Playlist.query.get_or_404(playlist_id)
    data = request.json

    next_position = db.session.query(func.max(PlaylistItem.position)).filter_by(playlist_id=playlist_id).scalar() or 0
    next_position += 1

    if data.get('media_id'):
        media = Media.query.get_or_404(data.get('media_id'))
        item = PlaylistItem(
            playlist_id=playlist_id,
            media_id=media.id,
            position=next_position,
            duration=_item_duration_from_payload(data, media),
            clip_start=float(data['clip_start']) if data.get('clip_start') not in (None, '', 0) else None,
            clip_end=float(data['clip_end'])   if data.get('clip_end')   not in (None, '', 0) else None,
            transition=_clean_transition(data.get('transition')),
            aspect_mode=_clean_aspect(data.get('aspect_mode')),
            mute_audio=bool(data.get('mute_audio', False)),
        )
    elif data.get('plugin_type'):
        try:
            plugin_duration = max(0, int(data.get('duration') or 0))
        except (TypeError, ValueError):
            plugin_duration = 30
        item = PlaylistItem(
            playlist_id=playlist_id,
            position=next_position,
            plugin_type=data.get('plugin_type'),
            plugin_config=data.get('plugin_config', {}),
            duration=plugin_duration,
            transition=_clean_transition(data.get('transition')),
            aspect_mode=_clean_aspect(data.get('aspect_mode')),
            mute_audio=bool(data.get('mute_audio', False)),
        )
    else:
        return jsonify({
            'status': 'error',
            'message': 'Either media_id or plugin_type is required'
        }), 400

    db.session.add(item)
    db.session.commit()

    return jsonify({
        'status': 'success',
        'message': 'Item added to playlist',
        'item': item.to_dict()
    })


# Bulk-add: append multiple media items in a single round-trip. Each
# entry can be a bare integer (media_id) or a dict with optional
# duration / transition / aspect_mode / mute_audio. Order is preserved.
@playlists_bp.route('/api/playlists/<int:playlist_id>/items/bulk', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_bulk_add_playlist_items(playlist_id):
    Playlist.query.get_or_404(playlist_id)
    data = request.get_json(silent=True) or {}
    raw_items = data.get('items') or []
    if not isinstance(raw_items, list) or not raw_items:
        return jsonify({'status': 'error',
                        'message': 'items[] required'}), 400

    default_transition = _clean_transition(data.get('transition'))
    default_duration   = data.get('duration')

    next_position = (db.session.query(func.max(PlaylistItem.position))
                     .filter_by(playlist_id=playlist_id).scalar() or 0)

    created = []
    skipped = []
    for raw in raw_items:
        spec = raw if isinstance(raw, dict) else {'media_id': raw}
        try:
            mid = int(spec.get('media_id'))
        except (TypeError, ValueError):
            skipped.append({'spec': raw, 'reason': 'invalid media_id'})
            continue
        media = Media.query.get(mid)
        if media is None:
            skipped.append({'media_id': mid, 'reason': 'not found'})
            continue
        next_position += 1
        item = PlaylistItem(
            playlist_id=playlist_id,
            media_id=media.id,
            position=next_position,
            duration=_item_duration_from_payload(
                spec if 'duration' in spec else ({'duration': default_duration} if default_duration is not None else {}),
                media),
            transition=_clean_transition(spec.get('transition', default_transition)),
            aspect_mode=_clean_aspect(spec.get('aspect_mode')),
            mute_audio=bool(spec.get('mute_audio', False)),
        )
        db.session.add(item)
        created.append(item)
    db.session.commit()
    return jsonify({
        'status': 'success',
        'added': [i.to_dict() for i in created],
        'skipped': skipped,
    })


# Bulk-update: apply a single patch (duration / transition / aspect_mode /
# mute_audio) across many items in this playlist.
@playlists_bp.route('/api/playlists/<int:playlist_id>/items/bulk-update', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_bulk_update_playlist_items(playlist_id):
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    patch = data.get('patch') or {}
    if not isinstance(ids, list) or not ids or not isinstance(patch, dict):
        return jsonify({'status': 'error',
                        'message': 'ids[] and patch{} required'}), 400

    items = (PlaylistItem.query
             .filter(PlaylistItem.playlist_id == playlist_id,
                     PlaylistItem.id.in_(ids))
             .all())
    for item in items:
        if 'duration' in patch:
            try:
                applied_duration = None
                if (isinstance(patch.get('duration'), str)
                        and patch.get('duration').strip().lower() == 'detected'):
                    if item.media and item.media.media_type == 'video':
                        applied_duration = detected_media_duration(item.media)
                        item.duration = applied_duration
                    else:
                        continue
                else:
                    applied_duration = max(0, int(patch['duration'] or 0))
                    item.duration = applied_duration
                # Plugin items also store their duration inside plugin_config
                # (that's what the plugin runtime reads to schedule its own
                # `signage:complete` postMessage). Mirror the bulk value so
                # the plugin actually honours the new duration instead of
                # silently using its previously-saved one.
                if item.plugin_type:
                    cfg = dict(item.plugin_config or {})
                    cfg['duration'] = applied_duration
                    item.plugin_config = cfg
            except (TypeError, ValueError):
                pass
        if 'transition' in patch:
            item.transition = _clean_transition(patch['transition'])
        if 'aspect_mode' in patch:
            item.aspect_mode = _clean_aspect(patch['aspect_mode'])
        if 'mute_audio' in patch:
            v = patch['mute_audio']
            # 'toggle' flips each item's current state independently so the
            # bulk Toggle Mute button works correctly on mixed selections.
            if isinstance(v, str) and v.strip().lower() == 'toggle':
                item.mute_audio = not bool(item.mute_audio)
            else:
                item.mute_audio = bool(v)
    db.session.commit()
    return jsonify({'status': 'success',
                    'updated': [i.to_dict() for i in items]})


# Bulk-delete items in a playlist. Re-numbers positions afterwards so
# the remaining items stay 1..N contiguous.
@playlists_bp.route('/api/playlists/<int:playlist_id>/items/bulk-delete', methods=['POST'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_bulk_delete_playlist_items(playlist_id):
    data = request.get_json(silent=True) or {}
    ids = data.get('ids') or []
    if not isinstance(ids, list) or not ids:
        return jsonify({'status': 'error',
                        'message': 'ids[] required'}), 400
    items = (PlaylistItem.query
             .filter(PlaylistItem.playlist_id == playlist_id,
                     PlaylistItem.id.in_(ids))
             .all())
    deleted = len(items)
    for item in items:
        db.session.delete(item)
    db.session.flush()
    # Re-pack positions.
    remaining = (PlaylistItem.query
                 .filter_by(playlist_id=playlist_id)
                 .order_by(PlaylistItem.position.asc())
                 .all())
    for idx, it in enumerate(remaining, start=1):
        it.position = idx
    db.session.commit()
    return jsonify({'status': 'success', 'deleted': deleted,
                    'remaining': len(remaining)})


@playlists_bp.route('/api/playlists/<int:playlist_id>/items/<int:item_id>', methods=['DELETE'])
@api_auth_required(['playlist:write'])
@require_permission('playlist.edit')
def api_delete_playlist_item(playlist_id, item_id):
    item = PlaylistItem.query.filter_by(id=item_id, playlist_id=playlist_id).first_or_404()
    deleted_position = item.position
    db.session.delete(item)
    items_to_update = PlaylistItem.query.filter(
        PlaylistItem.playlist_id == playlist_id,
        PlaylistItem.position > deleted_position
    ).all()
    for item in items_to_update:
        item.position -= 1
    db.session.commit()
    return jsonify({
        'status': 'success',
        'message': 'Item deleted from playlist'
    })
    
@playlists_bp.route('/api/display/playlist', methods=['GET'])
def api_display_playlist():
    """
    Returns the entire playlist for the requesting display.
    Uses the API key in the Authorization header ("Bearer ...").
    """
    # 1) Authenticate by API key (unchanged)
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.lower().startswith("bearer "):
        return jsonify({'status': 'error', 'message': 'Missing or invalid API key format'}), 401
    
    api_key = auth_header.split(" ", 1)[1].strip()
    display = Display.query.filter_by(api_key=api_key).first()
    if not display:
        return jsonify({'status': 'error', 'message': 'No display found with provided API key'}), 404

    client_ip = _real_client_ip()
    conflict = _check_single_client(display, client_ip)
    if conflict:
        return conflict

    # Update display status and last ping time (unchanged)
    display.status = 'online'
    display.last_ping = datetime.now()
    display.ip_address = client_ip
    db.session.commit()

    # 2) Find active schedule (existing logic unchanged)
    now = datetime.now()
    today = now.date()
    current_time = now.time()

    # Schedule inheritance: a display picks up schedules attached to
    # its own group AND every ancestor group up to the root. Lets an
    # admin attach a single schedule to a parent group and have it
    # cover every descendant display automatically.
    from groups import resolve_effective_group_ids
    effective_group_ids = resolve_effective_group_ids(display)

    potential_schedules = []
    display_schedules = Schedule.query.filter(
        Schedule.is_active == True,
        Schedule.display_id == display.id
    ).order_by(Schedule.priority.desc(), Schedule.id.asc()).all()
    potential_schedules.extend(display_schedules)
    if effective_group_ids:
        group_schedules = Schedule.query.filter(
            Schedule.is_active == True,
            Schedule.group_id.in_(effective_group_ids)
        ).order_by(Schedule.priority.desc(), Schedule.id.asc()).all()
        potential_schedules.extend(group_schedules)

    valid_schedules = []
    for sched in potential_schedules:
        date_valid = True
        if sched.start_date and sched.start_date > today:
            date_valid = False
        if sched.end_date and sched.end_date < today:
            date_valid = False

        day_valid = True
        if sched.days_of_week:
            daynum = now.isoweekday()
            if str(daynum) not in sched.days_of_week.split(","):
                day_valid = False

        time_valid = True
        if sched.start_time and sched.end_time:
            if sched.start_time < sched.end_time:
                if current_time < sched.start_time or current_time > sched.end_time:
                    time_valid = False
            else:
                if current_time < sched.start_time and current_time > sched.end_time:
                    time_valid = False

        if date_valid and day_valid and time_valid:
            valid_schedules.append(sched)

    if not valid_schedules:
        # Fallback: any schedule for this display or any of its
        # effective groups. Walks the same inheritance chain.
        fb_filter = Schedule.display_id == display.id
        if effective_group_ids:
            fb_filter = fb_filter | Schedule.group_id.in_(effective_group_ids)
        fallback_schedule = Schedule.query.filter(fb_filter).first()
        if fallback_schedule:
            valid_schedules = [fallback_schedule]
            logger.warning(f"No active schedule found for display {display.name}. Using fallback schedule.")
        else:
            return jsonify({'status': 'error', 'message': 'No schedules found for this display'}), 404

    chosen_schedule = valid_schedules[0]

    # 3) Load playlist and items (unchanged structure)
    playlist = Playlist.query.get(chosen_schedule.playlist_id)
    if not playlist:
        return jsonify({'status': 'error','message': f'Playlist not found (ID: {chosen_schedule.playlist_id})'}), 404
    if not playlist.items:
        return jsonify({'status': 'error','message': f'No items in playlist "{playlist.name}" (ID: {playlist.id})'}), 404

    playlist_items = sorted(playlist.items, key=lambda x: x.position)
    # Per-display capability filter (Phase 4). Items the display
    # cannot render (e.g. mismatched codec) are dropped before the
    # response is built. Skipped items are surfaced via
    # `playlist.excluded_items` for operator visibility.
    #
    # Same as display_player._resolve_playlist: synchronized groups use a
    # group-common capability filter so every player keeps the same item list.
    from capabilities import filter_items_for_display, filter_items_for_sync_group
    from sync_playback import group_sync_playback_active
    if group_sync_playback_active(display):
        playlist_items, _excluded = filter_items_for_sync_group(playlist_items, display)
    else:
        playlist_items, _excluded = filter_items_for_display(playlist_items, display)
    if not playlist_items:
        return jsonify({'status': 'error',
                        'message': f'All items in playlist "{playlist.name}" '
                                    'were filtered by this display\'s capabilities.',
                        'excluded_items': _excluded}), 404

    playlist_data = {
        'id': playlist.id,
        'name': playlist.name,
        'items': [],
        'excluded_items': _excluded,
        # CHANGED: version is computed hash (not just timestamp)
        'version': compute_playlist_version(playlist),
        'default_transition': playlist.default_transition or 'cut',
        'schedule_id': chosen_schedule.id,
        'display_settings': {
            'resolution_x': display.resolution_x,
            'resolution_y': display.resolution_y,
            'aspect_mode': display.aspect_mode,
            'orientation': display.orientation,
            'show_media_buttons': bool(display.show_media_buttons) and not group_sync_playback_active(display),
            'volume': int(display.volume if display.volume is not None else 100),
        }
    }

    # Resolve effective per-item transition once, server-side, so the player
    # only needs a single field. Item override wins; otherwise the playlist
    # default applies; 'random' is forwarded so the player can pick per slide.
    pl_default = (playlist.default_transition or 'cut').lower()

    def _effective_transition(item):
        raw = (item.transition or '').strip().lower()
        # The schema default is 'cut'; treat that as "inherit" so users who
        # never opened the per-item editor still get the playlist default.
        if raw and raw != 'cut':
            return raw
        return pl_default or 'cut'

    # Resolve effective video audio: per-item mute > playlist override >
    # per-media default. Mirrors the logic in display_player._resolve_playlist.
    pl_audio_default = (getattr(playlist, 'video_audio_default', None) or 'inherit').lower()

    def _effective_audio(item):
        media = item.media
        if not media or media.media_type != 'video':
            return True
        if item.mute_audio:
            return False
        if pl_audio_default == 'on':
            return True
        if pl_audio_default == 'off':
            return False
        return bool(getattr(media, 'audio_enabled', True))

    # 4) Build items; CHANGED: plugin items now get an HTTP URL via plugin runner
    for item in playlist_items:
        item_data = {
            'id': item.id,
            'position': item.position,
            'duration': item.duration,
            'effective_duration': playlist_item_duration_seconds(item),
            'transition':  _effective_transition(item),
            'aspect_mode': item.aspect_mode,
            'mute_audio':  not _effective_audio(item),
            'audio_enabled': _effective_audio(item),
            'clip_start':  item.clip_start,
            'clip_end':    item.clip_end,
            'plugin_type': item.plugin_type,
            'content_url': None,
            'media': None
        }

        media = item.media
        if media:
            if media.media_type == "webpage":
                item_data['content_url'] = media.file_path
            elif media.media_type in ("image", "video"):
                # Use the actual stored rel_path (tenant-scoped under dN/),
                # NOT the legacy 'images/<filename>' guess. Sign the URL so
                # the player can fetch over plain HTTP without cookies.
                item_data['content_url'] = (
                    storage.signed_url(media.file_path, external=True)
                    if media.file_path and not media.file_path.startswith(('http://', 'https://'))
                    else media.file_path
                )
            else:
                item_data['content_url'] = media.file_path

            item_data['media'] = {
                'id': media.id,
                'name': media.name,
                'media_type': media.media_type,
                'duration': media.duration,
                'duration_seconds': media.duration_seconds,
                'file_size': media.file_size,
                'thumbnail_url': storage.signed_url(media.thumbnail_path, external=True) if media.thumbnail_path else None
            }
        elif item.plugin_type:
            # Make plugins look like webpages for the client
            try:
                run_url = build_plugin_url(item.plugin_type, item.plugin_config or {})
            except Exception as e:
                logger.error(f"Failed to build plugin URL for {item.plugin_type}: {e}")
                run_url = url_for('plugins.run_plugin', plugin_type=item.plugin_type, _external=True)

            # Optional: nicer label
            meta = None
            try:
                meta = get_plugin_meta(item.plugin_type)
            except Exception:
                meta = None

            # Resolve effective sandbox attrs for the active tenant.
            # Player uses these to set the iframe `sandbox=` and `allow=`
            # attributes, so each plugin runs with the minimum required
            # capabilities rather than the broad legacy "allow-scripts
            # allow-same-origin allow-forms allow-popups allow-presentation".
            from plugin_system import resolve_plugin_policy, compute_sandbox_attrs
            from tenant_filter import current_domain_id
            try:
                pol = resolve_plugin_policy(item.plugin_type, current_domain_id())
            except Exception:
                pol = {'enabled': True, 'granted_permissions': []}
            sb = compute_sandbox_attrs(pol.get('granted_permissions') or [])

            item_data['content_url'] = run_url
            # Important compatibility hints for clients:
            item_data['media_type'] = 'webpage'
            item_data['plugin'] = {
                'type': item.plugin_type,
                'key':  (meta or {}).get('key') or item.plugin_type,
                'config': item.plugin_config or {},
                'name': (meta or {}).get('name') or (item.plugin_type or 'Plugin'),
                'enabled':     pol.get('enabled', True),
                'permissions': pol.get('granted_permissions') or [],
                'sandbox':     sb['sandbox'],
                'allow':       sb['allow'],
            }
        playlist_data['items'].append(item_data)

    playlist_data['last_updated'] = now.isoformat()

    # Synchronized playback: when the display's group has sync_playback
    # turned on, attach a wall-clock anchor so every display in the
    # group converges on the same slide index. See sync_playback.py for
    # the protocol.
    try:
        from sync_playback import build_sync_payload
        sync_block = build_sync_payload(display, playlist_data['items'],
                                         playlist_data['version'])
        if sync_block is not None:
            playlist_data['sync'] = sync_block
    except Exception as e:
        logger.warning(f'sync_playback: payload build failed: {e}')

    logger.info(f"Display {display.name} (ID: {display.id}) retrieved full playlist: schedule={chosen_schedule.name}, playlist={playlist.name}, items={len(playlist_items)}")

    return jsonify({'status': 'success', 'playlist': playlist_data})

@playlists_bp.route('/api/display/next', methods=['GET'])
def api_display_next():
    """
    Returns the next content to display for the requesting display.
    Uses the API key in the Authorization header ("Bearer ...").
    Considers display, group, schedule, playlist, and media configuration.
    """
    # 1) Authenticate by API key (unchanged)
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.lower().startswith("bearer "):
        return jsonify({'status': 'error', 'message': 'Missing or invalid API key format - use "Bearer YOUR_API_KEY"'}), 401
    api_key = auth_header.split(" ", 1)[1].strip()
    display = Display.query.filter_by(api_key=api_key).first()
    if not display:
        return jsonify({'status': 'error','message': 'No display found with the provided API key'}), 404

    client_ip = _real_client_ip()
    conflict = _check_single_client(display, client_ip)
    if conflict:
        return conflict

    display.status = 'online'
    display.last_ping = datetime.now()
    display.ip_address = client_ip
    db.session.commit()

    # 2) Find active schedule (unchanged logic)
    now = datetime.now()
    today = now.date()
    current_time = now.time()

    # Schedule inheritance: walk up through ancestor groups so
    # parent-group schedules apply to every descendant display.
    from groups import resolve_effective_group_ids
    effective_group_ids = resolve_effective_group_ids(display)

    potential_schedules = []
    display_schedules = Schedule.query.filter(
        Schedule.is_active == True,
        Schedule.display_id == display.id
    ).order_by(Schedule.priority.desc(), Schedule.id.asc()).all()
    potential_schedules.extend(display_schedules)
    if effective_group_ids:
        group_schedules = Schedule.query.filter(
            Schedule.is_active == True,
            Schedule.group_id.in_(effective_group_ids)
        ).order_by(Schedule.priority.desc(), Schedule.id.asc()).all()
        potential_schedules.extend(group_schedules)

    valid_schedules = []
    for sched in potential_schedules:
        date_valid = True
        if sched.start_date and sched.start_date > today:
            date_valid = False
        if sched.end_date and sched.end_date < today:
            date_valid = False

        day_valid = True
        if sched.days_of_week:
            daynum = now.isoweekday()
            if str(daynum) not in sched.days_of_week.split(","):
                day_valid = False

        time_valid = True
        if sched.start_time and sched.end_time:
            if sched.start_time < sched.end_time:
                if current_time < sched.start_time or current_time > sched.end_time:
                    time_valid = False
            else:
                if current_time < sched.start_time and current_time > sched.end_time:
                    time_valid = False

        if date_valid and day_valid and time_valid:
            valid_schedules.append(sched)

    if not valid_schedules:
        fb_filter = Schedule.display_id == display.id
        if effective_group_ids:
            fb_filter = fb_filter | Schedule.group_id.in_(effective_group_ids)
        fallback_schedule = Schedule.query.filter(fb_filter).first()
        if fallback_schedule:
            valid_schedules = [fallback_schedule]
            logger.warning(f"No active schedule found for display {display.name} at current time. Using fallback schedule.")
        else:
            return jsonify({
                'status': 'error',
                'message': 'No schedules found for this display. Please create at least one schedule.',
                'display_id': display.id,
                'display_name': display.name,
                'group_id': display.group_id,
                'effective_group_ids': effective_group_ids,
                'current_time': now.isoformat()
            }), 404

    chosen_schedule = valid_schedules[0]

    # 3) Get playlist and next item (unchanged core logic)
    playlist = Playlist.query.get(chosen_schedule.playlist_id)
    if not playlist:
        return jsonify({'status': 'error','message': f'Playlist not found (ID: {chosen_schedule.playlist_id})','schedule_id': chosen_schedule.id}), 404
    if not playlist.items:
        return jsonify({'status': 'error','message': f'No items in playlist \"{playlist.name}\" (ID: {playlist.id})','schedule_id': chosen_schedule.id,'playlist_id': playlist.id}), 404

    items = sorted(playlist.items, key=lambda x: x.position)
    item_ids = [item.id for item in items]
    last_item_id = display.current_playlist_item_id
    try:
        last_index = item_ids.index(last_item_id)
    except (ValueError, TypeError):
        last_index = -1
    next_index = (last_index + 1) % len(items)
    item = items[next_index]

    display.current_playlist_item_id = item.id
    db.session.commit()

    # 4) Build content_url (CHANGED: plugin items use HTTP plugin runner)
    content_url = None
    media = item.media
    if media:
        if media.media_type == "webpage":
            content_url = media.file_path
        elif media.media_type in ("image", "video"):
            # Sign the actual stored path; works for both legacy and tenant
            # layouts (signed_url short-circuits external URLs).
            content_url = (
                storage.signed_url(media.file_path, external=True)
                if media.file_path and not media.file_path.startswith(('http://', 'https://'))
                else media.file_path
            )
        else:
            content_url = media.file_path
    elif item.plugin_type:
        # CHANGED: use plugin runner URL so client treats as webpage
        content_url = build_plugin_url(item.plugin_type, item.plugin_config or {})

    display.current_content = content_url if content_url else "None"
    db.session.commit()

    # 5) Response (CHANGED: include playlist.version)
    resp = {
         'status': 'success',
         'display': {
             'id': display.id,
             'name': display.name,
             # Display has no single 'resolution' column anymore -- compose
             # the legacy "WxH" string for back-compat with older clients.
             'resolution': (f'{display.resolution_x}x{display.resolution_y}'
                            if display.resolution_x and display.resolution_y
                            else None),
             'orientation': display.orientation
         },
         'display_settings': {
             'resolution_x': display.resolution_x,
             'resolution_y': display.resolution_y,
             'aspect_mode': display.aspect_mode,
             'orientation': display.orientation,
         },
         'schedule': {
             'id': chosen_schedule.id,
             'name': chosen_schedule.name
         },
         'playlist': {
             'id': playlist.id,
             'name': playlist.name,
             'version': compute_playlist_version(playlist)
         },
         'item': {
             'id': item.id,
             'position': item.position,
             'duration': item.duration,
             'plugin_type': item.plugin_type,
             # Key compatibility signal: treat plugin as webpage for the player
             'media_type': ('webpage' if item.plugin_type else (item.media.media_type if item.media else None)),
             'plugin': ({
                 'type': item.plugin_type,
                 'config': item.plugin_config or {}
             } if item.plugin_type else None)
         },
         'content_url': content_url,
         'now': now.isoformat()
     }
     
     # Retain your existing 'media' block for real media:
    if media:
        resp['media'] = {
            'id': media.id,
            'name': media.name,
            'media_type': media.media_type,
            'duration': media.duration,
            'thumbnail_url': storage.signed_url(media.thumbnail_path, external=True) if media.thumbnail_path else None
        }

    return jsonify(resp)
