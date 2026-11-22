"""The "Add media" picker of a playlist: filtered, paged and fast with hundreds of images.

GET /playlists/<id>/media-picker returns JSON. Filters: q (name), type (image, video, web, plugin), plugin (a plugin key), source
("uploads" = what was uploaded or added by hand, the default; "all"; or an image source id), sort (newest, name), page. The answer
also carries the ids of everything that matches (up to MAX_IDS) so "select all matching" works across pages, and counts for the filter chips.
"""
from flask import jsonify, request, url_for
from flask_login import login_required
from sqlalchemy import func

from ..extensions import db
from ..models import ImageSource, Media, PlaylistItem
from ..models.media import MEDIA_IMAGE, MEDIA_PLUGIN, MEDIA_TYPES
from . import bp

PER_PAGE = 48
MAX_IDS = 1000


def _filtered(args, playlist_id: int):
    q = Media.query
    media_type = args.get("type", "")
    if media_type in MEDIA_TYPES:
        q = q.filter(Media.media_type == media_type)
    plugin = (args.get("plugin") or "").strip()
    if plugin:
        q = q.filter(Media.media_type == MEDIA_PLUGIN, Media.plugin_key == plugin[:32])
    source = args.get("source", "uploads")
    if source == "all":
        pass
    elif source.isdigit():
        q = q.filter(Media.source_id == int(source))
    else:
        q = q.filter(Media.source_id.is_(None))
    text = (args.get("q") or "").strip()
    if text:
        q = q.filter(Media.name.ilike("%" + text.replace("%", "").replace("_", "") + "%"))
    if args.get("hide_added") == "1":
        q = q.filter(~Media.id.in_(db.session.query(PlaylistItem.media_id).filter(PlaylistItem.playlist_id == playlist_id)))
    return q


def _entry(m: Media, in_playlist: set, source_names: dict) -> dict:
    return {"id": m.id, "name": m.name, "type": m.media_type, "plugin": m.plugin_key or None,
            "thumb": url_for("media.thumb", media_id=m.id, v=(m.thumbnail_name or "")[:8]) if m.thumbnail_name else None,
            "pending": m.thumbnail_status == "pending", "source": source_names.get(m.source_id), "added": m.id in in_playlist}


@bp.route("/<int:playlist_id>/media-picker")
@login_required
def media_picker(playlist_id: int):
    from .routes import _get_playlist_or_404
    _get_playlist_or_404(playlist_id)
    args = request.args
    q = _filtered(args, playlist_id)
    order = (func.lower(Media.name), Media.id) if args.get("sort") == "name" else (Media.created_at.desc(), Media.id.desc())
    total = q.count()
    page = max(1, args.get("page", 1, type=int) or 1)
    rows = q.order_by(*order).offset((page - 1) * PER_PAGE).limit(PER_PAGE).all()
    ids = [i for (i,) in q.order_by(*order).with_entities(Media.id).limit(MAX_IDS).all()]
    in_playlist = {i for (i,) in db.session.query(PlaylistItem.media_id).filter(PlaylistItem.playlist_id == playlist_id)}
    sources = ImageSource.query.order_by(ImageSource.name).all()
    names = {s.id: s.name for s in sources}
    # Chip counts follow the other filters (source, search) but not the chip's own type.
    base = _filtered({k: v for k, v in args.items() if k not in ("type", "plugin")}, playlist_id)
    types = dict(base.with_entities(Media.media_type, func.count(Media.id)).group_by(Media.media_type).all())
    plugins = [{"key": k, "count": c} for k, c in base.filter(Media.media_type == MEDIA_PLUGIN).with_entities(Media.plugin_key, func.count(Media.id))
               .group_by(Media.plugin_key).order_by(Media.plugin_key).all() if k]
    counts = dict(db.session.query(Media.source_id, func.count(Media.id)).group_by(Media.source_id).all())
    return jsonify(items=[_entry(m, in_playlist, names) for m in rows], total=total, page=page, pages=max(1, -(-total // PER_PAGE)), ids=ids,
                   truncated=total > MAX_IDS, types={t: types.get(t, 0) for t in MEDIA_TYPES}, plugins=plugins,
                   sources=[{"id": s.id, "name": s.name, "count": counts.get(s.id, 0)} for s in sources], uploads=counts.get(None, 0))
