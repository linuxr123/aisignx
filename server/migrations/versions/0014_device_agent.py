"""device agent: per-display agent settings and the device_commands table

Revision ID: b6f1d3a85c72
Revises: a4e6c8d02b57
Create Date: 2026-11-15 23:00:00.000000

Plain ADD COLUMN / CREATE TABLE, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b6f1d3a85c72'
down_revision = 'a4e6c8d02b57'
branch_labels = None
depends_on = None


def upgrade():
    for name in ('agent_watchdog', 'agent_allow_restart', 'agent_allow_logs'):
        op.add_column('displays', sa.Column(name, sa.Boolean(), nullable=False,
                                            server_default=sa.false()))
    op.create_table(
        'device_commands',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('display_id', sa.Integer(), sa.ForeignKey('displays.id', ondelete='CASCADE'),
                  nullable=False),
        sa.Column('request_id', sa.String(length=32), nullable=False),
        sa.Column('action', sa.String(length=32), nullable=False),
        sa.Column('params', sa.JSON(), nullable=True),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('result', sa.String(length=200), nullable=True),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'),
                  nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('delivered_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('acked_at', sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint('request_id', name='uq_device_commands_request_id'),
    )
    op.create_index('ix_device_commands_display_id', 'device_commands', ['display_id'])
    op.create_index('ix_device_commands_state', 'device_commands', ['state'])


def downgrade():
    op.drop_index('ix_device_commands_state', table_name='device_commands')
    op.drop_index('ix_device_commands_display_id', table_name='device_commands')
    op.drop_table('device_commands')
    for name in ('agent_allow_logs', 'agent_allow_restart', 'agent_watchdog'):
        op.drop_column('displays', name)
