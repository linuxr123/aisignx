"""displays.brightness and night dimming: how bright the screen is, and when it dims

Revision ID: b9f3d5a82c14
Revises: a8e2c4b17d93
Create Date: 2026-11-21 22:00:00.000000

Plain ADD COLUMN, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b9f3d5a82c14'
down_revision = 'a8e2c4b17d93'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('displays', sa.Column('brightness', sa.Integer(), nullable=False, server_default='100'))
    op.add_column('displays', sa.Column('dim_level', sa.Integer(), nullable=False, server_default='100'))
    op.add_column('displays', sa.Column('dim_from', sa.String(5), nullable=True))
    op.add_column('displays', sa.Column('dim_to', sa.String(5), nullable=True))


def downgrade():
    op.drop_column('displays', 'dim_to')
    op.drop_column('displays', 'dim_from')
    op.drop_column('displays', 'dim_level')
    op.drop_column('displays', 'brightness')
