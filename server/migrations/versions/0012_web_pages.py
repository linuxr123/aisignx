"""web pages: embeddable check and screenshot mode (web_mode, web_refresh_s, web_embeddable,
web_checked_at, snapshot_name, snapshot_sha256, snapshot_size, snapshot_at)

Revision ID: 9d3b7e5f2a14
Revises: 8c2f4a6d1e93
Create Date: 2026-11-15 18:00:00.000000

Plain ADD/DROP COLUMN only, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9d3b7e5f2a14'
down_revision = '8c2f4a6d1e93'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('media', sa.Column('web_mode', sa.String(length=12), nullable=False,
                                     server_default='auto'))
    op.add_column('media', sa.Column('web_refresh_s', sa.Integer(), nullable=False,
                                     server_default='300'))
    op.add_column('media', sa.Column('web_embeddable', sa.Boolean(), nullable=True))
    op.add_column('media', sa.Column('web_checked_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('media', sa.Column('snapshot_name', sa.String(length=64), nullable=True))
    op.add_column('media', sa.Column('snapshot_sha256', sa.String(length=64), nullable=True))
    op.add_column('media', sa.Column('snapshot_size', sa.BigInteger(), nullable=True))
    op.add_column('media', sa.Column('snapshot_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    for column in ('snapshot_at', 'snapshot_size', 'snapshot_sha256', 'snapshot_name',
                   'web_checked_at', 'web_embeddable', 'web_refresh_s', 'web_mode'):
        op.drop_column('media', column)
