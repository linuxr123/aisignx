"""displays.group_controls: whether a control on this screen moves its whole synchronized group

Revision ID: d4a7b9c13e58
Revises: c1d6e8a24f37
Create Date: 2026-11-26 21:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009). Existing screens keep the behaviour they have
now (on): a control on a screen of a synchronized group moves the whole group.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4a7b9c13e58'
down_revision = 'c1d6e8a24f37'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('displays', sa.Column('group_controls', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('displays', 'group_controls')
