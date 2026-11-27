"""display_groups.sync_controls: whether a control on one screen moves the whole synchronized group

Revision ID: e5b8c0d24f69
Revises: d4a7b9c13e58
Create Date: 2026-11-26 22:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009). Existing groups keep the behaviour they have now (on).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5b8c0d24f69'
down_revision = 'd4a7b9c13e58'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('display_groups', sa.Column('sync_controls', sa.Boolean(), nullable=False, server_default=sa.true()))


def downgrade():
    op.drop_column('display_groups', 'sync_controls')
