"""media: video_info (codec, profile, level, pixel format, frame rate, bitrate)

Revision ID: 8c2f4a6d1e93
Revises: 5b1e9c7d3a20
Create Date: 2026-11-15 15:00:00.000000

Plain ADD/DROP COLUMN only, never batch_alter_table (a batch rebuild of `media` would
cascade-delete playlist_items on SQLite; see 0009). Existing videos are probed in the
background by the media.probe job.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8c2f4a6d1e93'
down_revision = '5b1e9c7d3a20'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('media', sa.Column('video_info', sa.JSON(), nullable=True))


def downgrade():
    op.drop_column('media', 'video_info')
