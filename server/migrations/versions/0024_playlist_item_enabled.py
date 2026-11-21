"""playlist_items.enabled: switch an item off without removing it

Revision ID: f7d1b9c46a8c
Revises: e6c0a8b35f7b
Create Date: 2026-11-21 19:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f7d1b9c46a8c'
down_revision = 'e6c0a8b35f7b'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('playlist_items', sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('playlist_items', 'enabled')
