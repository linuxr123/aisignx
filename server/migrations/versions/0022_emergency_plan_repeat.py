"""emergency_plans.repeat: a saved message can repeat (daily, weekdays, weekly)

Revision ID: d5b9f7a24e6a
Revises: c4a8e6f13d59
Create Date: 2026-11-21 15:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd5b9f7a24e6a'
down_revision = 'c4a8e6f13d59'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('emergency_plans', sa.Column('repeat', sa.String(10), nullable=False, server_default='none'))


def downgrade():
    op.drop_column('emergency_plans', 'repeat')
