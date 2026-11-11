"""Playlist operations kept out of the routes so they're easy to test and reuse."""
from datetime import datetime, timezone

from ..extensions import db
from ..models import Media, Playlist, PlaylistItem


def touch(playlist: Playlist) -> None:
    """Mark the playlist as changed (players will use this to know when to reload)."""
    playlist.updated_at = datetime.now(timezone.utc)


def _renumber(playlist: Playlist) -> None:
    for index, item in enumerate(sorted(playlist.items, key=lambda i: (i.position, i.id or 0))):
        item.position = index


def add_media(playlist: Playlist, media_ids: list[int]) -> int:
    """Append media to the end of the playlist, in the given order. Returns the count added."""
    wanted = list(dict.fromkeys(media_ids))  # de-duplicate, keep order
    found = {m.id: m for m in Media.query.filter(Media.id.in_(wanted))} if wanted else {}
    position = max((i.position for i in playlist.items), default=-1) + 1
    added = 0
    for media_id in wanted:
        media = found.get(media_id)
        if media is None:
            continue
        playlist.items.append(PlaylistItem(media=media, position=position))
        position += 1
        added += 1
    if added:
        touch(playlist)
    return added


def remove_item(playlist: Playlist, item: PlaylistItem) -> None:
    playlist.items.remove(item)  # delete-orphan cascade deletes the row
    _renumber(playlist)
    touch(playlist)


def move_item(playlist: Playlist, item: PlaylistItem, offset: int) -> None:
    items = list(playlist.items)
    index = items.index(item)
    target = max(0, min(len(items) - 1, index + offset))
    items.insert(target, items.pop(index))
    for position, it in enumerate(items):
        it.position = position
    touch(playlist)


def reorder(playlist: Playlist, item_ids: list[int]) -> bool:
    """Apply a full new order. Returns False if the ids don't match the playlist exactly."""
    current = {item.id: item for item in playlist.items}
    if len(item_ids) != len(current) or set(item_ids) != set(current):
        return False
    for position, item_id in enumerate(item_ids):
        current[item_id].position = position
    touch(playlist)
    return True


def duplicate(playlist: Playlist) -> Playlist:
    copy = Playlist(
        name=f"{playlist.name} (copy)"[:120],
        description=playlist.description,
        default_transition=playlist.default_transition,
    )
    for item in playlist.items:
        copy.items.append(PlaylistItem(
            media_id=item.media_id, position=item.position, duration=item.duration,
            transition=item.transition, fit_mode=item.fit_mode, mute=item.mute,
        ))
    db.session.add(copy)
    return copy
