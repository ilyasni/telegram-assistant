"""add resolve status fields for error tracking and lease management

Revision ID: 20260116_add_resolve_status_fields
Revises: 20260114_add_access_hash_and_preferred_account
Create Date: 2026-01-16

Context7: Добавление полей resolve_error_code, resolve_next_at, resolve_lease_until и resolve_is_terminal
в channels для детализации ошибок резолва, управления lease-блокировками и разделения terminal/retryable ошибок.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260116_add_resolve_status'
down_revision = '20260114_add_channel_access'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление полей resolve_error_code, resolve_next_at, resolve_lease_until и resolve_is_terminal в channels."""
    
    # 1. Добавить колонку resolve_error_code (VARCHAR(50), nullable)
    # Детализация ошибок: 'ok', 'username_invalid', 'migrated_or_username_changed', 'not_channel', 
    # 'private', 'banned', 'floodwait', 'timeout', 'rpc_error', 'no_access'
    op.add_column(
        'channels',
        sa.Column('resolve_error_code', sa.String(length=50), nullable=True)
    )
    
    # 2. Добавить колонку resolve_next_at (TIMESTAMPTZ, nullable)
    # Когда пытаться снова (для retryable ошибок)
    op.add_column(
        'channels',
        sa.Column('resolve_next_at', sa.DateTime(timezone=True), nullable=True)
    )
    
    # 3. Добавить колонку resolve_lease_until (TIMESTAMPTZ, nullable)
    # TTL для блокировки (lease) при параллельном запуске скриптов
    # Автоматически освобождается через 10 минут
    op.add_column(
        'channels',
        sa.Column('resolve_lease_until', sa.DateTime(timezone=True), nullable=True)
    )
    
    # 4. Добавить колонку resolve_is_terminal (BOOLEAN, default=False)
    # Флаг для terminal ошибок (username_invalid, not_channel, banned)
    # Отличает terminal ошибки от retryable (floodwait, timeout, rpc_error)
    op.add_column(
        'channels',
        sa.Column('resolve_is_terminal', sa.Boolean(), nullable=False, server_default='false')
    )
    
    # Context7: Индексы для оптимизации запросов
    # Индекс для поиска каналов без lease-блокировки (resolve_lease_until IS NULL OR resolve_lease_until < NOW())
    op.create_index(
        'ix_channels_resolve_lease_until',
        'channels',
        ['resolve_lease_until'],
        unique=False,
        postgresql_where=sa.text("resolve_lease_until IS NOT NULL")
    )
    
    # Индекс для поиска каналов готовых к резолву (resolve_next_at IS NULL OR resolve_next_at < NOW())
    op.create_index(
        'ix_channels_resolve_next_at',
        'channels',
        ['resolve_next_at'],
        unique=False,
        postgresql_where=sa.text("resolve_next_at IS NOT NULL")
    )
    
    # Индекс для поиска по resolve_error_code (для диагностики)
    op.create_index(
        'ix_channels_resolve_error_code',
        'channels',
        ['resolve_error_code'],
        unique=False,
        postgresql_where=sa.text("resolve_error_code IS NOT NULL")
    )


def downgrade() -> None:
    """Откат изменений в channels."""
    
    # Удалить индексы
    op.drop_index('ix_channels_resolve_error_code', table_name='channels')
    op.drop_index('ix_channels_resolve_next_at', table_name='channels')
    op.drop_index('ix_channels_resolve_lease_until', table_name='channels')
    
    # Удалить колонки
    op.drop_column('channels', 'resolve_is_terminal')
    op.drop_column('channels', 'resolve_lease_until')
    op.drop_column('channels', 'resolve_next_at')
    op.drop_column('channels', 'resolve_error_code')
