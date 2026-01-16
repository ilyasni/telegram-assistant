"""create ingest_accounts table for service account pool

Revision ID: 20260117_create_ingest_accounts
Revises: 20260116_add_resolve_status
Create Date: 2026-01-17

Context7: Создание таблицы ingest_accounts для управления пулом сервисных аккаунтов,
используемых для парсинга публичных каналов. Изолирует сервисные аккаунты от пользовательских сессий
и позволяет распределять нагрузку между несколькими аккаунтами с защитой от FloodWait.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20260117_ingest_accounts'
down_revision = '20260116_add_resolve_status'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Создание таблицы ingest_accounts с начальными данными."""
    
    # Context7: Устанавливаем lock_timeout для безопасности
    op.execute("SET lock_timeout = '2min'")
    
    # Создание таблицы ingest_accounts
    op.create_table(
        'ingest_accounts',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('telegram_id', sa.BigInteger(), nullable=False, unique=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('priority', sa.Integer(), nullable=False, server_default='100'),  # меньше = выше приоритет
        sa.Column('role', sa.String(length=20), nullable=False, server_default=sa.text("'both'")),  # 'read', 'resolver', 'both'
        sa.Column('max_concurrent_channels', sa.Integer(), nullable=True),
        sa.Column('blocked_until', sa.DateTime(timezone=True), nullable=True),  # защита от FloodWait
        sa.Column('last_error_code', sa.Text(), nullable=True),
        sa.Column('last_error_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),  # медленный аудит (debounce)
        sa.Column('notes', sa.Text(), nullable=True),
    )
    
    # Индексы
    op.create_index(
        'idx_ingest_accounts_telegram_id',
        'ingest_accounts',
        ['telegram_id'],
        unique=True
    )
    
    # Context7: Составной индекс для быстрого фильтра активных незаблокированных аккаунтов
    # WHERE is_active = true AND (blocked_until IS NULL OR blocked_until <= NOW())
    op.create_index(
        'idx_ingest_accounts_active_blocked_priority',
        'ingest_accounts',
        ['is_active', 'blocked_until', 'priority'],
        unique=False
    )
    
    # Context7: Триггер для автоматического обновления updated_at
    op.execute("""
        CREATE OR REPLACE FUNCTION update_ingest_accounts_updated_at()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    
    op.execute("""
        CREATE TRIGGER trigger_update_ingest_accounts_updated_at
        BEFORE UPDATE ON ingest_accounts
        FOR EACH ROW
        EXECUTE FUNCTION update_ingest_accounts_updated_at();
    """)
    
    # Context7: Начальные данные - существующие сервисные аккаунты
    op.execute("""
        INSERT INTO ingest_accounts (telegram_id, priority, role, is_active, notes, created_at)
        VALUES 
            (8124731874, 1, 'both', true, 'Основной collector аккаунт', NOW()),
            (8592423875, 2, 'both', true, 'Дополнительный сервисный аккаунт', NOW())
        ON CONFLICT (telegram_id) DO NOTHING;
    """)


def downgrade() -> None:
    """Откат изменений - удаление таблицы ingest_accounts."""
    
    # Удалить триггер
    op.execute("DROP TRIGGER IF EXISTS trigger_update_ingest_accounts_updated_at ON ingest_accounts;")
    op.execute("DROP FUNCTION IF EXISTS update_ingest_accounts_updated_at();")
    
    # Удалить индексы
    op.drop_index('idx_ingest_accounts_active_blocked_priority', table_name='ingest_accounts')
    op.drop_index('idx_ingest_accounts_telegram_id', table_name='ingest_accounts')
    
    # Удалить таблицу
    op.drop_table('ingest_accounts')
