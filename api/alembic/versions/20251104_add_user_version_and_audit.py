"""add user version and audit log for OCC and change tracking [Context7: OCC]

Revision ID: 20251104_add_user_version_and_audit
Revises: 20250131_add_user_role
Create Date: 2025-11-04

Context7: Добавляем версионирование для оптимистичной блокировки (OCC) и таблицу аудита
для отслеживания изменений tier/role администраторами.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '20251104_user_version'
down_revision = '20250131_add_user_role'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Context7: Безопасное добавление колонок с использованием lock_timeout для предотвращения зависаний
    # Используем прямой SQL с IF NOT EXISTS для защиты от повторного запуска
    
    # Context7: Увеличиваем lock_timeout до 2 минут для production окружений с активной нагрузкой
    # Если блокировка не получена за 2 минуты - откатываем миграцию (лучше явная ошибка, чем зависание)
    op.execute("SET lock_timeout = '2min'")
    
    # Context7: Проверяем наличие колонок перед добавлением (защита от повторного запуска)
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    existing_columns = {col['name'] for col in inspector.get_columns('users')}
    
    # Шаг 1: Добавляем колонки как nullable (безопасно для больших таблиц)
    # Context7: Используем IF NOT EXISTS проверку через Python для избежания блокировок на проверке
    if 'version' not in existing_columns:
        op.execute("ALTER TABLE users ADD COLUMN version INTEGER")
    
    if 'updated_at' not in existing_columns:
        op.execute("ALTER TABLE users ADD COLUMN updated_at TIMESTAMP WITH TIME ZONE")
    
    # Шаг 3: Backfill существующих пользователей (безопасно, не блокирует)
    op.execute("""
        UPDATE users 
        SET version = 1, 
            updated_at = COALESCE(created_at, NOW())
        WHERE version IS NULL
    """)
    
    # Шаг 4: Устанавливаем NOT NULL и DEFAULT (может потребовать блокировку, но кратковременно)
    # Context7: Выполняем только если колонка ещё nullable
    op.execute("""
        DO $$
        BEGIN
            -- Устанавливаем NOT NULL и DEFAULT для version
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'users' 
                AND column_name = 'version' 
                AND is_nullable = 'YES'
            ) THEN
                ALTER TABLE users 
                    ALTER COLUMN version SET DEFAULT 1,
                    ALTER COLUMN version SET NOT NULL;
            END IF;
            
            -- Устанавливаем NOT NULL и DEFAULT для updated_at
            IF EXISTS (
                SELECT 1 FROM information_schema.columns 
                WHERE table_name = 'users' 
                AND column_name = 'updated_at' 
                AND is_nullable = 'YES'
            ) THEN
                ALTER TABLE users 
                    ALTER COLUMN updated_at SET DEFAULT NOW(),
                    ALTER COLUMN updated_at SET NOT NULL;
            END IF;
        END $$;
    """)
    
    # Context7: Создаём таблицу аудита для истории изменений tier/role
    op.create_table(
        'user_audit_log',
        sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('action', sa.String(50), nullable=False),  # role_changed|tier_changed|upgraded|downgraded
        sa.Column('old_value', sa.String(255)),
        sa.Column('new_value', sa.String(255)),
        sa.Column('changed_by', postgresql.UUID(as_uuid=True)),  # admin user_id
        sa.Column('changed_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('notes', sa.Text()),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['changed_by'], ['users.id'], ondelete='SET NULL'),
    )
    
    # Context7: Индексы для быстрого поиска истории изменений
    op.create_index('ix_user_audit_log_user_id', 'user_audit_log', ['user_id'])
    op.create_index('ix_user_audit_log_changed_at', 'user_audit_log', ['changed_at'])
    op.create_index('ix_user_audit_log_action', 'user_audit_log', ['action'])
    
    # Context7: Триггер для автоматического обновления updated_at при изменении записи
    op.execute("""
        CREATE OR REPLACE FUNCTION update_updated_at_column()
        RETURNS TRIGGER AS $$
        BEGIN
            NEW.updated_at = NOW();
            RETURN NEW;
        END;
        $$ language 'plpgsql';
    """)
    
    op.execute("""
        CREATE TRIGGER update_users_updated_at
        BEFORE UPDATE ON users
        FOR EACH ROW
        EXECUTE FUNCTION update_updated_at_column();
    """)


def downgrade() -> None:
    # Context7: Откат триггера и функции
    op.execute("DROP TRIGGER IF EXISTS update_users_updated_at ON users")
    op.execute("DROP FUNCTION IF EXISTS update_updated_at_column()")
    
    # Удаляем таблицу аудита
    op.drop_index('ix_user_audit_log_action', table_name='user_audit_log')
    op.drop_index('ix_user_audit_log_changed_at', table_name='user_audit_log')
    op.drop_index('ix_user_audit_log_user_id', table_name='user_audit_log')
    op.drop_table('user_audit_log')
    
    # Удаляем колонки версионирования
    op.drop_column('users', 'updated_at')
    op.drop_column('users', 'version')

