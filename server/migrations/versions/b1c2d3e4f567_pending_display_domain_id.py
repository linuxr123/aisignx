"""pending_display_domain_id

Revision ID: b1c2d3e4f567
Revises: 53a8dc53ac78
Create Date: 2026-05-02 00:00:00.000000

Adds PendingDisplay.domain_id so device registrations are tied to the tenant
they intend to join, preventing other tenants from approving/hijacking the
device before setup completes.
"""
from alembic import op
import sqlalchemy as sa


revision = 'b1c2d3e4f567'
down_revision = '53a8dc53ac78'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('pending_display', schema=None) as batch_op:
        batch_op.add_column(sa.Column('domain_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_pending_display_domain_id'),
                              ['domain_id'], unique=False)
        batch_op.create_foreign_key('fk_pending_display_domain_id',
                                    'domain', ['domain_id'], ['id'])


def downgrade():
    with op.batch_alter_table('pending_display', schema=None) as batch_op:
        batch_op.drop_constraint('fk_pending_display_domain_id', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_pending_display_domain_id'))
        batch_op.drop_column('domain_id')
