"""scheduling: display groups, schedules, emergency messages

Revision ID: b7d3e1a95c20
Revises: 6432f461876d
Create Date: 2026-11-11 23:30:00.000000

"""
from contextlib import contextmanager

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d3e1a95c20'
down_revision = '6432f461876d'
branch_labels = None
depends_on = None

# env.py has no naming_convention, so the displays.group_id FK is named explicitly.
FK_DISPLAY_GROUP = 'fk_displays_group_id_display_groups'


@contextmanager
def _rebuilding_displays():
    """Keep pairing codes while SQLite rebuilds `displays`.

    The batch rebuild drops the old table, and with foreign keys on, SQLite's DROP TABLE
    cascades into pairing_codes (ON DELETE CASCADE). Toggling PRAGMA foreign_keys is not
    an option: it is ignored inside a transaction, and turning it back on afterwards
    would fail the same way, leaving a pooled connection without FK enforcement. So copy
    the rows aside and put them back. Other databases alter in place.
    """
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        yield
        return
    codes = sa.Table('pairing_codes', sa.MetaData(), autoload_with=bind)
    rows = [dict(r) for r in bind.execute(sa.select(codes)).mappings()]
    yield
    bind.execute(sa.delete(codes))
    if rows:
        bind.execute(sa.insert(codes), rows)


def upgrade():
    op.create_table('display_groups',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('display_groups', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_display_groups_name'), ['name'], unique=True)

    with _rebuilding_displays(), op.batch_alter_table('displays', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(FK_DISPLAY_GROUP, 'display_groups', ['group_id'], ['id'],
                                    ondelete='SET NULL')
        batch_op.create_index(batch_op.f('ix_displays_group_id'), ['group_id'], unique=False)

    op.create_table('schedules',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('playlist_id', sa.Integer(), nullable=False),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('display_id', sa.Integer(), nullable=True),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('days_mask', sa.Integer(), nullable=False),
    sa.Column('start_time', sa.Time(), nullable=True),
    sa.Column('end_time', sa.Time(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=True),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('priority', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("scope IN ('all', 'group', 'display')", name='ck_schedules_scope'),
    sa.CheckConstraint(
        "(scope = 'all' AND display_id IS NULL AND group_id IS NULL) OR "
        "(scope = 'display' AND display_id IS NOT NULL AND group_id IS NULL) OR "
        "(scope = 'group' AND group_id IS NOT NULL AND display_id IS NULL)",
        name='ck_schedules_target'),
    sa.CheckConstraint('days_mask BETWEEN 1 AND 127', name='ck_schedules_days'),
    sa.CheckConstraint('(start_time IS NULL) = (end_time IS NULL)',
                       name='ck_schedules_time_pair'),
    sa.CheckConstraint('start_time IS NULL OR start_time <> end_time',
                       name='ck_schedules_time_ne'),
    sa.CheckConstraint('start_date IS NULL OR end_date IS NULL OR end_date >= start_date',
                       name='ck_schedules_dates'),
    sa.CheckConstraint('priority BETWEEN 0 AND 100', name='ck_schedules_priority'),
    sa.ForeignKeyConstraint(['playlist_id'], ['playlists.id'], ondelete='RESTRICT'),
    sa.ForeignKeyConstraint(['display_id'], ['displays.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['group_id'], ['display_groups.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_schedules_playlist_id'), ['playlist_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_schedules_display_id'), ['display_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_schedules_group_id'), ['group_id'], unique=False)

    op.create_table('emergency_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=120), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('show_text', sa.Boolean(), nullable=False),
    sa.Column('playlist_id', sa.Integer(), nullable=True),
    sa.Column('scope', sa.String(length=10), nullable=False),
    sa.Column('display_id', sa.Integer(), nullable=True),
    sa.Column('group_id', sa.Integer(), nullable=True),
    sa.Column('target_label', sa.String(length=120), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('cleared_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('cleared_by_id', sa.Integer(), nullable=True),
    sa.Column('clear_reason', sa.String(length=20), nullable=True),
    sa.CheckConstraint("level IN ('info', 'warning', 'critical')", name='ck_emergency_level'),
    sa.CheckConstraint("scope IN ('all', 'group', 'display')", name='ck_emergency_scope'),
    sa.CheckConstraint("scope <> 'all' OR (display_id IS NULL AND group_id IS NULL)",
                       name='ck_emergency_all'),
    sa.CheckConstraint('NOT (display_id IS NOT NULL AND group_id IS NOT NULL)',
                       name='ck_emergency_one'),
    sa.ForeignKeyConstraint(['playlist_id'], ['playlists.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['display_id'], ['displays.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['group_id'], ['display_groups.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['created_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['cleared_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('emergency_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_emergency_messages_playlist_id'), ['playlist_id'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_emergency_messages_display_id'), ['display_id'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_emergency_messages_group_id'), ['group_id'],
                              unique=False)
        batch_op.create_index(batch_op.f('ix_emergency_messages_cleared_at'), ['cleared_at'],
                              unique=False)


def downgrade():
    with op.batch_alter_table('emergency_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_emergency_messages_cleared_at'))
        batch_op.drop_index(batch_op.f('ix_emergency_messages_group_id'))
        batch_op.drop_index(batch_op.f('ix_emergency_messages_display_id'))
        batch_op.drop_index(batch_op.f('ix_emergency_messages_playlist_id'))

    op.drop_table('emergency_messages')
    with op.batch_alter_table('schedules', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_schedules_group_id'))
        batch_op.drop_index(batch_op.f('ix_schedules_display_id'))
        batch_op.drop_index(batch_op.f('ix_schedules_playlist_id'))

    op.drop_table('schedules')
    with _rebuilding_displays(), op.batch_alter_table('displays', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_displays_group_id'))
        batch_op.drop_constraint(FK_DISPLAY_GROUP, type_='foreignkey')
        batch_op.drop_column('group_id')

    with op.batch_alter_table('display_groups', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_display_groups_name'))

    op.drop_table('display_groups')
