"""enrollment_codes

Revision ID: c2d3e4f56789
Revises: b1c2d3e4f567
Create Date: 2026-05-10 00:00:00.000000

Adds proof-of-invitation device enrollment:
  - Domain.enrollment_code (rotatable secret, unique, indexed)
  - Domain.enrollment_code_expires_at (optional expiry)
  - Domain.enrollment_enabled (kill-switch)
  - PendingDisplay.enrollment_code_used (audit trail)
  - PendingDisplay.user_agent (audit trail)

Without an enrollment_code, /api/register and /api/register/browser will
refuse to create a pending row, so a hostile/misconfigured device can no
longer spam other tenants' approval queues.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c2d3e4f56789'
down_revision = 'b1c2d3e4f567'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('domain', schema=None) as batch_op:
        batch_op.add_column(sa.Column('enrollment_code', sa.String(length=40),
                                      nullable=True))
        batch_op.add_column(sa.Column('enrollment_code_expires_at',
                                      sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('enrollment_enabled', sa.Boolean(),
                                      nullable=False, server_default=sa.true()))
        batch_op.create_index(batch_op.f('ix_domain_enrollment_code'),
                              ['enrollment_code'], unique=True)

    with op.batch_alter_table('pending_display', schema=None) as batch_op:
        batch_op.add_column(sa.Column('enrollment_code_used',
                                      sa.String(length=40), nullable=True))
        batch_op.add_column(sa.Column('user_agent',
                                      sa.String(length=255), nullable=True))
        batch_op.create_index(batch_op.f('ix_pending_display_enrollment_code_used'),
                              ['enrollment_code_used'], unique=False)


def downgrade():
    with op.batch_alter_table('pending_display', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pending_display_enrollment_code_used'))
        batch_op.drop_column('user_agent')
        batch_op.drop_column('enrollment_code_used')

    with op.batch_alter_table('domain', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_domain_enrollment_code'))
        batch_op.drop_column('enrollment_enabled')
        batch_op.drop_column('enrollment_code_expires_at')
        batch_op.drop_column('enrollment_code')
