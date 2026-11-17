"""device_commands.seq: time-based replay counter for signed commands

Revision ID: a2e6c8f95b13
Revises: f1d5b7e84a69
Create Date: 2026-11-16 21:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a2e6c8f95b13'
down_revision = 'f1d5b7e84a69'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('device_commands', sa.Column('seq', sa.BigInteger(), nullable=True))


def downgrade():
    op.drop_column('device_commands', 'seq')
