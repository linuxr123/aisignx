"""plugins: plugin items are media rows with a plugin key and a JSON config

Revision ID: 3d8f6b2a91c4
Revises: a7c4e2f9b851
Create Date: 2026-11-14 12:00:00.000000

Plain ADD/DROP COLUMN only, never batch_alter_table: a batch rebuild of `media` would
cascade-delete playlist_items on SQLite (the same trap 0007 documents for `displays`).
SQLite 3.35+ drops these columns natively once their index is gone.

Downgrade removes plugin items first (their playlist items, then the media rows), since
an older schema has no way to describe them. Their thumbnail files become orphans,
which the media.orphans job removes.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3d8f6b2a91c4'
down_revision = 'a7c4e2f9b851'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('media', sa.Column('plugin_key', sa.String(length=32), nullable=True))
    op.add_column('media', sa.Column('plugin_config', sa.JSON(), nullable=True))
    op.create_index('ix_media_plugin_key', 'media', ['plugin_key'])


def downgrade():
    op.execute("DELETE FROM playlist_items WHERE media_id IN "
               "(SELECT id FROM media WHERE media_type = 'plugin')")
    op.execute("DELETE FROM media WHERE media_type = 'plugin'")
    op.drop_index('ix_media_plugin_key', table_name='media')
    op.drop_column('media', 'plugin_config')
    op.drop_column('media', 'plugin_key')
