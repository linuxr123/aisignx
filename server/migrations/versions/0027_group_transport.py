"""display_groups.transport: shared play/pause/skip state of a synchronized group

Revision ID: c1d6e8a24f37
Revises: b9f3d5a82c14
Create Date: 2026-11-26 09:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d6e8a24f37'
down_revision = 'b9f3d5a82c14'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('display_groups', sa.Column('transport', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('display_groups', 'transport')
