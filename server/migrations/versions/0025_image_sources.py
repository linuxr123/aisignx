"""image_sources and media.source_*: images read from folders, shares and cloud albums

Revision ID: a8e2c4b17d93
Revises: f7d1b9c46a8c
Create Date: 2026-11-21 21:00:00.000000

Plain CREATE TABLE / ADD COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a8e2c4b17d93'
down_revision = 'f7d1b9c46a8c'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'image_sources',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('kind', sa.String(12), nullable=False),
        sa.Column('location', sa.String(1024), nullable=False),
        sa.Column('username', sa.String(120), nullable=False, server_default=''),
        sa.Column('secret', sa.Text(), nullable=True),
        sa.Column('recursive', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('mirror', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('interval_min', sa.Integer(), nullable=False, server_default='60'),
        sa.Column('max_images', sa.Integer(), nullable=False, server_default='200'),
        sa.Column('next_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_status', sa.String(300), nullable=False, server_default=''),
        sa.Column('last_ok', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index('ix_image_sources_next_sync_at', 'image_sources', ['next_sync_at'])
    op.add_column('media', sa.Column('source_id', sa.Integer(), nullable=True))
    op.add_column('media', sa.Column('source_key', sa.String(700), nullable=True))
    op.add_column('media', sa.Column('source_stamp', sa.String(160), nullable=True))
    op.create_index('ix_media_source_id', 'media', ['source_id'])


def downgrade():
    op.drop_index('ix_media_source_id', table_name='media')
    op.drop_column('media', 'source_stamp')
    op.drop_column('media', 'source_key')
    op.drop_column('media', 'source_id')
    op.drop_table('image_sources')
