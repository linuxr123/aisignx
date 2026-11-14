"""player controls: lock player and auto-resume after a pause

Revision ID: a7c4e2f9b851
Revises: e5a9c3d17b42
Create Date: 2026-11-14 10:00:00.000000

Plain ADD/DROP COLUMN only, never batch_alter_table: a batch rebuild of `displays` would
cascade-delete pairing_codes and play_events on SQLite (see 0005, 0006 and 0007). SQLite
3.35+ drops these columns natively; neither is indexed, constrained or part of a foreign
key.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7c4e2f9b851'
down_revision = 'e5a9c3d17b42'
branch_labels = None
depends_on = None

COLUMNS = (
    ('kiosk_lock', sa.Boolean(), False, sa.false()),
    ('resume_after_s', sa.Integer(), False, '300'),
)


def upgrade():
    for name, type_, nullable, default in COLUMNS:
        op.add_column('displays', sa.Column(name, type_, nullable=nullable,
                                            server_default=default))


def downgrade():
    for name, _type, _nullable, _default in reversed(COLUMNS):
        op.drop_column('displays', name)
