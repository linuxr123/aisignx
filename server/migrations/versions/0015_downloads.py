"""downloads: installer files offered to signed-in users

Revision ID: c7a2e4b91d36
Revises: b6f1d3a85c72
Create Date: 2026-11-16 09:00:00.000000

Plain CREATE TABLE, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c7a2e4b91d36'
down_revision = 'b6f1d3a85c72'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'downloads',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(24), nullable=False),
        sa.Column('version', sa.String(32), nullable=False, server_default=''),
        sa.Column('filename', sa.String(160), nullable=False),
        sa.Column('stored_name', sa.String(64), nullable=False),
        sa.Column('size', sa.BigInteger(), nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('notes', sa.String(400), nullable=False, server_default=''),
        sa.Column('uploaded_by', sa.String(64), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('download_count', sa.Integer(), nullable=False, server_default='0'),
    )
    op.create_index('ix_downloads_kind', 'downloads', ['kind'])
    op.create_index('ix_downloads_stored_name', 'downloads', ['stored_name'], unique=True)


def downgrade():
    op.drop_index('ix_downloads_stored_name', table_name='downloads')
    op.drop_index('ix_downloads_kind', table_name='downloads')
    op.drop_table('downloads')
