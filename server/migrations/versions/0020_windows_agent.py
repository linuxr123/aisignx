"""privileged Windows agent: its own token, one-time enrollment code, public key and reported state

Revision ID: b3f7d9a14c26
Revises: a2e6c8f95b13
Create Date: 2026-11-17 09:00:00.000000

Plain ADD/DROP COLUMN and CREATE/DROP INDEX, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b3f7d9a14c26'
down_revision = 'a2e6c8f95b13'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('displays', sa.Column('agent_token_hash', sa.String(length=64), nullable=True))
    op.add_column('displays', sa.Column('agent_enroll_hash', sa.String(length=64), nullable=True))
    op.add_column('displays', sa.Column('agent_enroll_expires', sa.DateTime(timezone=True), nullable=True))
    op.add_column('displays', sa.Column('agent_enroll_fails', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('displays', sa.Column('agent_pubkey', sa.String(length=64), nullable=True))
    op.add_column('displays', sa.Column('agent_diag', sa.Text(), nullable=True))
    op.add_column('displays', sa.Column('agent_seen_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('displays', sa.Column('agent_enrolled_at', sa.DateTime(timezone=True), nullable=True))
    op.create_index('ix_displays_agent_token_hash', 'displays', ['agent_token_hash'], unique=True)
    op.create_index('ix_displays_agent_enroll_hash', 'displays', ['agent_enroll_hash'], unique=False)


def downgrade():
    op.drop_index('ix_displays_agent_enroll_hash', table_name='displays')
    op.drop_index('ix_displays_agent_token_hash', table_name='displays')
    for name in ('agent_enrolled_at', 'agent_seen_at', 'agent_diag', 'agent_pubkey', 'agent_enroll_fails',
                 'agent_enroll_expires', 'agent_enroll_hash', 'agent_token_hash'):
        op.drop_column('displays', name)
