"""add access_hash, preferred_account_id and collector subscription status to channels

Revision ID: 20260114_add_access_hash_and_preferred_account
Revises: 20260114_add_resolve_attempts
Create Date: 2026-01-14

Context7: Добавление полей access_hash, preferred_account_id, collector_subscribed_at и collector_subscription_status
в channels для поддержки пула сессий, стабильного резолва по InputPeerChannel и отслеживания подписки collector аккаунта.
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20260114_add_access_hash'
down_revision = '20260114_add_resolve_attempts'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление полей access_hash, preferred_account_id и статуса подписки collector в channels."""
    
    # 1. Добавить колонку access_hash (BIGINT, nullable)
    # Используется для стабильного резолва по InputPeerChannel без username
    op.add_column(
        'channels',
        sa.Column('access_hash', sa.BigInteger(), nullable=True)
    )
    
    # 2. Добавить колонку preferred_account_id (BIGINT, nullable)
    # telegram_id сессии, которая успешно резолвит канал
    op.add_column(
        'channels',
        sa.Column('preferred_account_id', sa.BigInteger(), nullable=True)
    )
    
    # 3. Добавить колонку collector_subscribed_at (TIMESTAMPTZ, nullable)
    # Когда collector аккаунт (8124731874) подписался на канал
    op.add_column(
        'channels',
        sa.Column('collector_subscribed_at', sa.DateTime(timezone=True), nullable=True)
    )
    
    # 4. Добавить колонку collector_subscription_status (VARCHAR(20), nullable)
    # Статус подписки collector: 'subscribed', 'failed', 'private', 'not_needed'
    op.add_column(
        'channels',
        sa.Column('collector_subscription_status', sa.String(length=20), nullable=True)
    )
    
    # Context7: Индексы для оптимизации запросов
    # Индекс для поиска каналов по preferred_account_id
    op.create_index(
        'ix_channels_preferred_account_id',
        'channels',
        ['preferred_account_id'],
        unique=False
    )
    
    # Индекс для поиска каналов без подписки collector
    op.create_index(
        'ix_channels_collector_subscription_status',
        'channels',
        ['collector_subscription_status'],
        unique=False
    )


def downgrade() -> None:
    """Откат изменений в channels."""
    
    # Удалить индексы
    op.drop_index('ix_channels_collector_subscription_status', table_name='channels')
    op.drop_index('ix_channels_preferred_account_id', table_name='channels')
    
    # Удалить колонки
    op.drop_column('channels', 'collector_subscription_status')
    op.drop_column('channels', 'collector_subscribed_at')
    op.drop_column('channels', 'preferred_account_id')
    op.drop_column('channels', 'access_hash')
