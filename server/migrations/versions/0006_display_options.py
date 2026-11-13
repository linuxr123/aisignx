"""display options: location, orientation, fit, volume, kiosk lock, diagnostics, screenshots

Revision ID: c4e8a2f61d37
Revises: b7d3e1a95c20
Create Date: 2026-11-12 10:00:00.000000

Plain ADD/DROP COLUMN only, never batch_alter_table: a batch rebuild of `displays` would
cascade-delete pairing_codes on SQLite (see 0005). SQLite 3.35+ drops these columns
natively; none of them is indexed, constrained or part of a foreign key.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c4e8a2f61d37'
down_revision = 'b7d3e1a95c20'
branch_labels = None
depends_on = None

COLUMNS = (
    ('location', sa.String(length=100), True, None),
    ('description', sa.Text(), True, None),
    ('orientation', sa.Integer(), False, '0'),
    ('fit_override', sa.String(length=16), True, None),
    ('volume', sa.Integer(), False, '100'),
    ('show_offline_banner', sa.Boolean(), False, sa.true()),
    ('allow_input', sa.Boolean(), False, sa.false()),
    ('unlock_pin_hash', sa.String(length=200), True, None),
    ('diagnostics', sa.Text(), True, None),
    ('diagnostics_at', sa.DateTime(timezone=True), True, None),
    ('screenshot_request', sa.String(length=32), True, None),
    ('screenshot_requested_at', sa.DateTime(timezone=True), True, None),
    ('screenshot_at', sa.DateTime(timezone=True), True, None),
)


def upgrade():
    for name, type_, nullable, default in COLUMNS:
        op.add_column('displays', sa.Column(name, type_, nullable=nullable,
                                            server_default=default))


def downgrade():
    for name, _type, _nullable, _default in reversed(COLUMNS):
        op.drop_column('displays', name)
