"""playlist items: volume (0-100); YouTube sound moves from the item to the playlist

Revision ID: 5b1e9c7d3a20
Revises: 3d8f6b2a91c4
Create Date: 2026-11-15 12:00:00.000000

The YouTube plugin's own Mute option is replaced by the playlist item's Mute box. So an
existing YouTube item that was set to play with sound keeps it: its playlist items are
unmuted here.
"""
import json

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5b1e9c7d3a20'
down_revision = '3d8f6b2a91c4'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('playlist_items', sa.Column('volume', sa.Integer(), nullable=False,
                                              server_default='100'))
    conn = op.get_bind()
    rows = conn.execute(sa.text(
        "SELECT id, plugin_config FROM media WHERE plugin_key = 'youtube'")).fetchall()
    for media_id, config in rows:
        if isinstance(config, str):
            try:
                config = json.loads(config)
            except ValueError:
                continue
        if isinstance(config, dict) and config.get("mute") is False:
            conn.execute(sa.text("UPDATE playlist_items SET mute = :m WHERE media_id = :id"),
                         {"m": False, "id": media_id})


def downgrade():
    op.drop_column('playlist_items', 'volume')
