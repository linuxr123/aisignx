"""scheduled emergencies: emergency_plans and emergency_messages.ends_at

Revision ID: e9c4a6d37f58
Revises: d8b3f5c26e47
Create Date: 2026-11-16 15:00:00.000000

Plain ADD COLUMN / CREATE TABLE, never batch_alter_table (see 0009).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e9c4a6d37f58'
down_revision = 'd8b3f5c26e47'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('emergency_messages', sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        'emergency_plans',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('template_id', sa.Integer(), sa.ForeignKey('emergency_templates.id', ondelete='CASCADE'), nullable=False),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('duration_min', sa.Integer(), nullable=True),
        sa.Column('state', sa.String(12), nullable=False, server_default='pending'),
        sa.Column('note', sa.String(200), nullable=False, server_default=''),
        sa.Column('message_id', sa.Integer(), sa.ForeignKey('emergency_messages.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
    )
    op.create_index('ix_emergency_plans_template_id', 'emergency_plans', ['template_id'])
    op.create_index('ix_emergency_plans_starts_at', 'emergency_plans', ['starts_at'])
    op.create_index('ix_emergency_plans_state', 'emergency_plans', ['state'])


def downgrade():
    op.drop_index('ix_emergency_plans_state', table_name='emergency_plans')
    op.drop_index('ix_emergency_plans_starts_at', table_name='emergency_plans')
    op.drop_index('ix_emergency_plans_template_id', table_name='emergency_plans')
    op.drop_table('emergency_plans')
    op.drop_column('emergency_messages', 'ends_at')
