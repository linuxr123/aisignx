"""add unlock_pin to display

Revision ID: a912b3c4d5e6
Revises: 8357d12eb496
Create Date: 2026-04-26 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
import random


# revision identifiers, used by Alembic.
revision = 'a912b3c4d5e6'
down_revision = '6f314b75096e'
branch_labels = None
depends_on = None


def upgrade():
    # Add the column nullable so the schema change succeeds on existing rows.
    with op.batch_alter_table('display', schema=None) as batch_op:
        batch_op.add_column(sa.Column('unlock_pin', sa.String(length=8), nullable=True))

    # Backfill: every existing display gets a random 4-digit PIN so kiosks
    # are locked-by-default the moment this migration runs. Admins can
    # change or clear the PIN from the edit-display page later.
    conn = op.get_bind()
    rows = conn.execute(sa.text("SELECT id FROM display")).fetchall()
    for r in rows:
        pin = ''.join(str(random.randint(0, 9)) for _ in range(4))
        conn.execute(
            sa.text("UPDATE display SET unlock_pin = :pin WHERE id = :id"),
            {"pin": pin, "id": r[0]}
        )


def downgrade():
    with op.batch_alter_table('display', schema=None) as batch_op:
        batch_op.drop_column('unlock_pin')
