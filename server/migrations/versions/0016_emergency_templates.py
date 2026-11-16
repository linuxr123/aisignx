"""emergency templates

Revision ID: d8b3f5c26e47
Revises: c7a2e4b91d36
Create Date: 2026-11-16 12:00:00.000000

Plain CREATE TABLE, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd8b3f5c26e47'
down_revision = 'c7a2e4b91d36'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'emergency_templates',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(80), nullable=False, unique=True),
        sa.Column('title', sa.String(120), nullable=False),
        sa.Column('message', sa.Text(), nullable=False, server_default=''),
        sa.Column('level', sa.String(10), nullable=False, server_default='critical'),
        sa.Column('show_text', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('playlist_id', sa.Integer(), sa.ForeignKey('playlists.id', ondelete='SET NULL'), nullable=True),
        sa.Column('target', sa.String(24), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )


def downgrade():
    op.drop_table('emergency_templates')
