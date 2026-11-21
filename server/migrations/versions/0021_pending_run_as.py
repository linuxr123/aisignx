"""displays.pending_run_as: run mode chosen at Windows set-up, applied once the agent is online

Revision ID: c4a8e6f13d59
Revises: b3f7d9a14c26
Create Date: 2026-11-21 12:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4a8e6f13d59'
down_revision = 'b3f7d9a14c26'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('displays', sa.Column('pending_run_as', sa.String(12), nullable=True))


def downgrade():
    op.drop_column('displays', 'pending_run_as')
