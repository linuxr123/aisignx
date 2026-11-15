"""display groups: sync_playback (synchronized playback of the group's displays)

Revision ID: a4e6c8d02b57
Revises: 9d3b7e5f2a14
Create Date: 2026-11-15 21:00:00.000000

Plain ADD/DROP COLUMN only, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4e6c8d02b57'
down_revision = '9d3b7e5f2a14'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('display_groups', sa.Column('sync_playback', sa.Boolean(), nullable=False,
                                              server_default=sa.false()))


def downgrade():
    op.drop_column('display_groups', 'sync_playback')
