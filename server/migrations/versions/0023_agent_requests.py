"""agent_requests and displays.agent_auto_approve: a PC's agent asks to be enrolled, the admin approves

Revision ID: e6c0a8b35f7b
Revises: d5b9f7a24e6a
Create Date: 2026-11-21 18:00:00.000000

Plain ADD COLUMN / CREATE TABLE, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6c0a8b35f7b'
down_revision = 'd5b9f7a24e6a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'agent_requests',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('claim_hash', sa.String(64), nullable=False),
        sa.Column('poll_hash', sa.String(64), nullable=False),
        sa.Column('public_key', sa.String(64), nullable=False),
        sa.Column('host', sa.String(64), nullable=False, server_default=''),
        sa.Column('version', sa.String(20), nullable=False, server_default=''),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('display_id', sa.Integer(), sa.ForeignKey('displays.id', ondelete='CASCADE'), nullable=True),
        sa.Column('state', sa.String(10), nullable=False, server_default='pending'),
        sa.Column('ciphertext', sa.Text(), nullable=True),
    )
    op.create_index('ix_agent_requests_claim_hash', 'agent_requests', ['claim_hash'], unique=True)
    op.create_index('ix_agent_requests_poll_hash', 'agent_requests', ['poll_hash'], unique=True)
    op.create_index('ix_agent_requests_expires_at', 'agent_requests', ['expires_at'])
    op.create_index('ix_agent_requests_display_id', 'agent_requests', ['display_id'])
    op.add_column('displays', sa.Column('agent_auto_approve', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    op.drop_column('displays', 'agent_auto_approve')
    op.drop_table('agent_requests')
