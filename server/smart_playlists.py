"""
smart_playlists.py
==================
Rules-based ("smart") playlist resolver.

A smart playlist stores a JSON rule blob in ``Playlist.smart_rules`` instead
of (or in addition to) ``PlaylistItem`` rows. At resolve time we query the
tenant's Media library, apply the rules, sort, cap, and synthesise transient
``PlaylistItem``-shaped objects so the rest of the player pipeline
(``display_player.py``, ``compute_playlist_version``, ``filter_items_for_display``,
etc.) doesn't need to special-case smart playlists.

Rule schema (v1):

    {
        "all_tags":         ["promo", "winter"],   # media must have ALL of these
        "any_tags":         ["sale", "ad"],        # AND at least one of these
        "exclude_tags":     ["draft"],             # AND none of these
        "media_types":      ["image", "video"],    # restrict to listed types
        "name_contains":    "2024",                # case-insensitive substring
        "folder":           "campaigns/winter",    # logical folder filter
        "folder_recursive": true                   # include subfolders (default)
    }

Order: 'newest' (default), 'oldest', 'name', 'random'.
Limit: defaults to 50, hard-capped at MAX_LIMIT.
"""
from datetime import datetime
from logging_config import logger
from models import Media


DEFAULT_LIMIT = 50
MAX_LIMIT = 500
ALLOWED_ORDERS = {'newest', 'oldest', 'name', 'random'}
ALLOWED_TYPES = {'image', 'video', 'webpage'}


class _TransientItem:
    """Duck-typed PlaylistItem stand-in returned by the resolver. Only the
    attributes the player + version hash + capability filter actually read
    are populated."""

    __slots__ = ('id', 'playlist_id', 'media_id', 'media', 'position',
                 'duration', 'aspect_mode', 'plugin_type', 'plugin_config',
                 'clip_start', 'clip_end', 'mute_audio', 'transition')

    def __init__(self, media, playlist, position):
        # Synthetic id: stable per (playlist, media) so version hashes change
        # only when the matched set changes, not on every page load.
        self.id = -(playlist.id * 1_000_000 + media.id)
        self.playlist_id = playlist.id
        self.media_id = media.id
        self.media = media
        self.position = position
        self.duration = media.duration or 10
        self.aspect_mode = None
        self.plugin_type = None
        self.plugin_config = None
        self.clip_start = None
        self.clip_end = None
        self.mute_audio = False
        self.transition = 'cut'


def _normalise_rules(raw):
    """Coerce a rules dict into the strict shape the resolver expects."""
    if not isinstance(raw, dict):
        return None

    def _str_list(v):
        if isinstance(v, str):
            v = [s.strip() for s in v.split(',')]
        if not isinstance(v, list):
            return []
        out = []
        for s in v:
            if not isinstance(s, str):
                continue
            s = s.strip().lower()
            if s:
                out.append(s)
        return out

    types = _str_list(raw.get('media_types'))
    types = [t for t in types if t in ALLOWED_TYPES]

    # Folder filter is case-preserved (folders are user-named) but
    # matching is case-insensitive to be forgiving. Empty string means
    # "no folder filter" (all folders).
    folder = (raw.get('folder') or '').strip().strip('/').replace('\\', '/')
    folder_recursive = bool(raw.get('folder_recursive', True))

    return {
        'all_tags':         _str_list(raw.get('all_tags')),
        'any_tags':         _str_list(raw.get('any_tags')),
        'exclude_tags':     _str_list(raw.get('exclude_tags')),
        'media_types':      types,
        'name_contains':    (raw.get('name_contains') or '').strip(),
        'folder':           folder,
        'folder_recursive': folder_recursive,
    }


def _media_tags(m):
    raw = (m.tags or '') if hasattr(m, 'tags') else ''
    return {t.strip().lower() for t in raw.split(',') if t.strip()}


def _matches(media, rules):
    if rules['media_types'] and media.media_type not in rules['media_types']:
        return False
    nc = rules['name_contains']
    if nc and nc.lower() not in (media.name or '').lower():
        return False
    f = rules.get('folder') or ''
    if f:
        mf = (getattr(media, 'folder', '') or '').strip('/').replace('\\', '/')
        f_norm = f.strip('/').lower()
        mf_norm = mf.lower()
        if rules.get('folder_recursive', True):
            if not (mf_norm == f_norm or mf_norm.startswith(f_norm + '/')):
                return False
        else:
            if mf_norm != f_norm:
                return False
    tags = _media_tags(media)
    if rules['all_tags'] and not all(t in tags for t in rules['all_tags']):
        return False
    if rules['any_tags'] and not any(t in tags for t in rules['any_tags']):
        return False
    if rules['exclude_tags'] and any(t in tags for t in rules['exclude_tags']):
        return False
    return True


def _ordered(matched, order):
    order = (order or 'newest').lower()
    if order not in ALLOWED_ORDERS:
        order = 'newest'
    if order == 'newest':
        return sorted(matched, key=lambda m: m.created_at or datetime.min, reverse=True)
    if order == 'oldest':
        return sorted(matched, key=lambda m: m.created_at or datetime.min)
    if order == 'name':
        return sorted(matched, key=lambda m: (m.name or '').lower())
    if order == 'random':
        import random
        out = list(matched)
        random.shuffle(out)
        return out
    return matched


def preview(playlist):
    """Return the matched Media list (NOT TransientItems). Useful for the
    editor's live preview and for tests."""
    rules = _normalise_rules(playlist._smart_rules_dict())
    if rules is None:
        return []
    # Tenant scoping: the global tenant_filter pins Media queries to the
    # current domain. Playlist resolution always runs in the display's
    # tenant context (set by _resolve_display_by_token / login session).
    matched = [m for m in Media.query.all() if _matches(m, rules)]
    matched = _ordered(matched, playlist.smart_order)
    limit = playlist.smart_limit or DEFAULT_LIMIT
    if limit < 1:
        limit = DEFAULT_LIMIT
    if limit > MAX_LIMIT:
        limit = MAX_LIMIT
    return matched[:limit]


def resolve_smart_items(playlist):
    """Return a list of transient ``PlaylistItem``-shaped objects for the
    player. Indexed positions start at 1 to mirror the manual-playlist
    convention."""
    items = []
    for i, m in enumerate(preview(playlist), start=1):
        items.append(_TransientItem(m, playlist, i))
    if not items:
        logger.info(f'smart playlist {playlist.id} resolved 0 items')
    return items
