"""Migration 0009 (plugins): upgrade, downgrade and upgrade again with plugin rows present."""
import sqlalchemy as sa
from flask_migrate import downgrade, upgrade

from aisignx import dbinfo
from aisignx.extensions import db
from aisignx.models import Media, Playlist, PlaylistItem

from .plugin_fixtures import add_item, papp  # noqa: F401

PREVIOUS = "a7c4e2f9b851"
REVISION = "3d8f6b2a91c4"


def _columns():
    return {c["name"] for c in sa.inspect(db.engine).get_columns("media")}


def _indexes():
    return {i["name"] for i in sa.inspect(db.engine).get_indexes("media")}


def _count(table):
    with db.engine.connect() as conn:
        return conn.execute(sa.text(f"SELECT COUNT(*) FROM {table}")).scalar()


def test_plugins_migration_round_trip(papp):
    plugin_id = add_item(papp)
    with papp.app_context():
        assert dbinfo.current_revision() == REVISION == dbinfo.head_revision()
        web = Media(name="W", media_type="web", url="https://example.com")
        playlist = Playlist(name="P")
        db.session.add(playlist)
        playlist.items.append(PlaylistItem(media=web, position=0))
        playlist.items.append(PlaylistItem(media=db.session.get(Media, plugin_id), position=1))
        db.session.commit()
        assert {"plugin_key", "plugin_config"} <= _columns()
        assert "ix_media_plugin_key" in _indexes()
        db.session.remove()

        downgrade(revision=PREVIOUS)
        db.engine.dispose()
        assert dbinfo.current_revision() == PREVIOUS
        assert not {"plugin_key", "plugin_config"} & _columns()
        assert "ix_media_plugin_key" not in _indexes()
        assert _count("media") == 1 and _count("playlist_items") == 1  # web item survives
        assert _count("playlists") == 1

        upgrade()
        db.engine.dispose()
        assert dbinfo.current_revision() == REVISION
        assert {"plugin_key", "plugin_config"} <= _columns()
        assert _count("playlist_items") == 1
        media = Media.query.one()
        assert media.media_type == "web" and media.plugin_key is None
