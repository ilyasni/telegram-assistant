"""add channel_access table for session analytics

Revision ID: 20260114_add_channel_access
Revises: 20260114_add_access_hash
Create Date: 2026-01-14

Context7: Создание таблицы channel_access для отслеживания истории успешных резолвов по сессиям,
автоматического выбора лучшей сессии и аналитики доступности каналов.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision = '20260114_add_channel_access'
down_revision = '20260114_add_access_hash'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создание таблицы channel_access для аналитики доступа сессий к каналам."""
    
    # Context7: Таблица для отслеживания доступа сессий к каналам
    op.create_table(
        'channel_access',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('channel_id', UUID(as_uuid=True), nullable=False),
        sa.Column('account_id', sa.BigInteger(), nullable=False),  # telegram_id сессии
        sa.Column('access_level', sa.String(length=20), nullable=True),  # 'public_ok', 'member_ok', 'no_access', 'not_found'
        sa.Column('last_ok_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('fail_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('NOW()')),
        
        # Context7: Foreign key на channels
        sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
        
        # Context7: Уникальный индекс для пары (channel_id, account_id)
        sa.UniqueConstraint('channel_id', 'account_id', name='uq_channel_access_channel_account')
    )
    
    # Context7: Индексы для оптимизации запросов
    # Индекс для поиска по account_id (какие каналы доступны сессии)
    op.create_index(
        'ix_channel_access_account_id',
        'channel_access',
        ['account_id'],
        unique=False
    )
    
    # Индекс для поиска по channel_id (какие сессии имеют доступ к каналу)
    op.create_index(
        'ix_channel_access_channel_id',
        'channel_access',
        ['channel_id'],
        unique=False
    )
    
    # Индекс для поиска успешных доступов (last_ok_at IS NOT NULL)
    op.create_index(
        'ix_channel_access_last_ok_at',
        'channel_access',
        ['last_ok_at'],
        unique=False,
        postgresql_where=sa.text('last_ok_at IS NOT NULL')
    )
    
    # Индекс для поиска по access_level
    op.create_index(
        'ix_channel_access_access_level',
        'channel_access',
        ['access_level'],
        unique=False
    )


def downgrade() -> None:
    """Откат изменений - удаление таблицы channel_access."""
    
    # Удалить индексы
    op.drop_index('ix_channel_access_access_level', table_name='channel_access')
    op.drop_index('ix_channel_access_last_ok_at', table_name='channel_access')
    op.drop_index('ix_channel_access_channel_id', table_name='channel_access')
    op.drop_index('ix_channel_access_account_id', table_name='channel_access')
    
    # Удалить таблицу
    op.drop_table('channel_access')
