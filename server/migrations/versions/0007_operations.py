"""operations: audit log, API tokens, alerts, proof of play, display alert mute

Revision ID: e5a9c3d17b42
Revises: c4e8a2f61d37
Create Date: 2026-11-12 22:10:00.000000

Purely additive. `displays.alerts_muted` uses plain ADD/DROP COLUMN, never
batch_alter_table: a batch rebuild of `displays` would cascade-delete pairing_codes and
play_events on SQLite (see 0005 and 0006). SQLite 3.35+ drops the column natively.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5a9c3d17b42'
down_revision = 'c4e8a2f61d37'
branch_labels = None
depends_on = None

_OPEN = sa.text('resolved_at IS NULL')


def upgrade():
    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('ts', sa.DateTime(timezone=True), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=64), nullable=True),
    sa.Column('via', sa.String(length=8), nullable=True),
    sa.Column('token_id', sa.Integer(), nullable=True),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('target_type', sa.String(length=32), nullable=True),
    sa.Column('target_id', sa.Integer(), nullable=True),
    sa.Column('target_name', sa.String(length=200), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_audit_log_ts', 'audit_log', ['ts'], unique=False)
    op.create_index('ix_audit_log_action_ts', 'audit_log', ['action', 'ts'], unique=False)
    op.create_index('ix_audit_log_user_id_ts', 'audit_log', ['user_id', 'ts'], unique=False)
    op.create_index('ix_audit_log_target', 'audit_log', ['target_type', 'target_id'],
                    unique=False)

    op.create_table('api_tokens',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('prefix', sa.String(length=16), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('scopes', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_ip', sa.String(length=45), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_api_tokens_token_hash', 'api_tokens', ['token_hash'], unique=True)
    op.create_index('ix_api_tokens_user_id', 'api_tokens', ['user_id'], unique=False)

    op.create_table('alerts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.String(length=32), nullable=False),
    sa.Column('key', sa.String(length=100), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('detail', sa.Text(), nullable=True),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('acknowledged_by_id', sa.Integer(), nullable=True),
    sa.Column('notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolve_notified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('notify_attempts', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_notify_error', sa.String(length=255), nullable=True),
    sa.ForeignKeyConstraint(['acknowledged_by_id'], ['users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    # At most one open alert per (kind, key).
    op.create_index('uq_alerts_open', 'alerts', ['kind', 'key'], unique=True,
                    sqlite_where=_OPEN, postgresql_where=_OPEN)

    op.create_table('play_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('display_id', sa.Integer(), nullable=False),
    sa.Column('client_event_id', sa.String(length=64), nullable=False),
    sa.Column('media_id', sa.Integer(), nullable=False),
    sa.Column('item_id', sa.Integer(), nullable=True),
    sa.Column('media_name', sa.String(length=200), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['display_id'], ['displays.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('display_id', 'client_event_id', name='uq_play_events_display_client')
    )
    op.create_index('ix_play_events_started_at', 'play_events', ['started_at'], unique=False)
    op.create_index('ix_play_events_received_at', 'play_events', ['received_at'],
                    unique=False)
    op.create_index('ix_play_events_media_started', 'play_events', ['media_id', 'started_at'],
                    unique=False)

    op.create_table('play_daily',
    sa.Column('day', sa.Date(), nullable=False),
    sa.Column('display_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('media_id', sa.Integer(), autoincrement=False, nullable=False),
    sa.Column('display_name', sa.String(length=120), nullable=True),
    sa.Column('media_name', sa.String(length=200), nullable=True),
    sa.Column('plays', sa.Integer(), nullable=False),
    sa.Column('seconds', sa.Integer(), nullable=False),
    sa.PrimaryKeyConstraint('day', 'display_id', 'media_id')
    )

    op.add_column('displays', sa.Column('alerts_muted', sa.Boolean(), nullable=False,
                                        server_default=sa.false()))


def downgrade():
    op.drop_column('displays', 'alerts_muted')
    op.drop_table('play_daily')
    op.drop_index('ix_play_events_media_started', table_name='play_events')
    op.drop_index('ix_play_events_received_at', table_name='play_events')
    op.drop_index('ix_play_events_started_at', table_name='play_events')
    op.drop_table('play_events')
    op.drop_index('uq_alerts_open', table_name='alerts')
    op.drop_table('alerts')
    op.drop_index('ix_api_tokens_user_id', table_name='api_tokens')
    op.drop_index('ix_api_tokens_token_hash', table_name='api_tokens')
    op.drop_table('api_tokens')
    op.drop_index('ix_audit_log_target', table_name='audit_log')
    op.drop_index('ix_audit_log_user_id_ts', table_name='audit_log')
    op.drop_index('ix_audit_log_action_ts', table_name='audit_log')
    op.drop_index('ix_audit_log_ts', table_name='audit_log')
    op.drop_table('audit_log')
