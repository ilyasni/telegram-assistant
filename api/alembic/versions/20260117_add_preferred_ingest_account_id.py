"""add preferred_ingest_account_id FK to channels

Revision ID: 20260117_add_preferred_ingest_account_id
Revises: 20260117_create_ingest_accounts
Create Date: 2026-01-17

Context7: Добавление preferred_ingest_account_id (UUID FK на ingest_accounts) в channels
для связи каналов с сервисными аккаунтами из пула. Сохраняем preferred_account_id (BIGINT)
для обратной совместимости (dual-write).
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260117_preferred_fk'
down_revision = '20260117_ingest_accounts'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление preferred_ingest_account_id в channels и миграция данных."""
    
    # Context7: Устанавливаем lock_timeout для безопасности
    op.execute("SET lock_timeout = '2min'")
    
    # 1. Добавить колонку preferred_ingest_account_id (UUID, FK на ingest_accounts)
    op.add_column(
        'channels',
        sa.Column('preferred_ingest_account_id', postgresql.UUID(as_uuid=True), nullable=True)
    )
    
    # 2. Создать внешний ключ
    op.create_foreign_key(
        'fk_channels_preferred_ingest_account_id',
        'channels',
        'ingest_accounts',
        ['preferred_ingest_account_id'],
        ['id'],
        ondelete='SET NULL'
    )
    
    # 3. Создать индекс для оптимизации запросов
    op.create_index(
        'ix_channels_preferred_ingest_account_id',
        'channels',
        ['preferred_ingest_account_id'],
        unique=False
    )
    
    # 4. Миграция данных: найти ingest_accounts по telegram_id = preferred_account_id
    # Context7: Обновляем preferred_ingest_account_id для каналов, у которых есть preferred_account_id
    op.execute("""
        UPDATE channels c
        SET preferred_ingest_account_id = ia.id
        FROM ingest_accounts ia
        WHERE c.preferred_account_id = ia.telegram_id
          AND c.preferred_account_id IS NOT NULL
          AND c.preferred_ingest_account_id IS NULL;
    """)


def downgrade() -> None:
    """Откат изменений - удаление preferred_ingest_account_id из channels."""
    
    # Удалить индекс
    op.drop_index('ix_channels_preferred_ingest_account_id', table_name='channels')
    
    # Удалить внешний ключ
    op.drop_constraint('fk_channels_preferred_ingest_account_id', 'channels', type_='foreignkey')
    
    # Удалить колонку
    op.drop_column('channels', 'preferred_ingest_account_id')
