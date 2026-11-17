"""signed device commands: per-display switches for reboot, startup changes and app updates

Revision ID: f1d5b7e84a69
Revises: e9c4a6d37f58
Create Date: 2026-11-16 20:00:00.000000

Plain ADD/DROP COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f1d5b7e84a69'
down_revision = 'e9c4a6d37f58'
branch_labels = None
depends_on = None

COLUMNS = ('agent_allow_reboot', 'agent_allow_startup', 'agent_allow_update')


def upgrade():
    for name in COLUMNS:
        op.add_column('displays', sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    for name in reversed(COLUMNS):
        op.drop_column('displays', name)
