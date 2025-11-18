"""add tenant storage usage tracking

Revision ID: 20250122_add_tenant_storage_usage
Revises: 20250121_add_source_field
Create Date: 2025-01-22 12:00:00.000000

Context7: Создание таблицы tenant_storage_usage для отслеживания использования S3 storage по tenant.
Используется для мониторинга использования и контроля квот.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '20250122_tenant_storage'  # Context7: Сокращенный ID для VARCHAR(32)
down_revision = '20250121_add_source_field'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Создание таблицы tenant_storage_usage для отслеживания использования S3 storage.
    
    Структура:
    - tenant_id: UUID tenant
    - content_type: тип контента (media|vision|crawl)
    - total_bytes: общий размер в байтах
    - objects_count: количество объектов
    - last_updated: время последнего обновления
    - created_at: время создания записи
    
    Использование:
    - Периодический расчет использования через StorageQuotaService
    - Prometheus метрики для мониторинга
    - Интеграция с budget_gate для контроля квот
    """
    
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    
    # Проверяем, существует ли таблица
    existing_tables = inspector.get_table_names()
    
    if 'tenant_storage_usage' not in existing_tables:
        op.create_table(
            'tenant_storage_usage',
            sa.Column('id', postgresql.UUID(as_uuid=True), primary_key=True, server_default=sa.text('gen_random_uuid()')),
            sa.Column('tenant_id', postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column('content_type', sa.String(20), nullable=False),  # media|vision|crawl
            sa.Column('total_bytes', sa.BigInteger(), nullable=False, server_default='0'),
            sa.Column('total_gb', sa.REAL(), nullable=False, server_default='0.0'),
            sa.Column('objects_count', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('last_updated', postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
            
            # Constraints
            sa.CheckConstraint(
                'total_bytes >= 0',
                name='chk_tenant_storage_bytes_positive'
            ),
            sa.CheckConstraint(
                'total_gb >= 0.0',
                name='chk_tenant_storage_gb_positive'
            ),
            sa.CheckConstraint(
                'objects_count >= 0',
                name='chk_tenant_storage_objects_positive'
            ),
            sa.CheckConstraint(
                "content_type IN ('media', 'vision', 'crawl')",
                name='chk_tenant_storage_content_type'
            ),
            
            # Unique constraint: один tenant_id + content_type
            sa.UniqueConstraint('tenant_id', 'content_type', name='uq_tenant_storage_tenant_content')
        )
        
        # Индексы для быстрого поиска
        op.create_index(
            'idx_tenant_storage_tenant_id',
            'tenant_storage_usage',
            ['tenant_id']
        )
        
        op.create_index(
            'idx_tenant_storage_content_type',
            'tenant_storage_usage',
            ['content_type']
        )
        
        op.create_index(
            'idx_tenant_storage_last_updated',
            'tenant_storage_usage',
            ['last_updated']
        )
        
        # Композитный индекс для быстрого поиска по tenant_id + content_type
        op.create_index(
            'idx_tenant_storage_tenant_content',
            'tenant_storage_usage',
            ['tenant_id', 'content_type']
        )


def downgrade() -> None:
    """Откат миграции: удаление таблицы tenant_storage_usage."""
    
    # Удаляем индексы
    op.execute("DROP INDEX IF EXISTS idx_tenant_storage_tenant_content")
    op.execute("DROP INDEX IF EXISTS idx_tenant_storage_last_updated")
    op.execute("DROP INDEX IF EXISTS idx_tenant_storage_content_type")
    op.execute("DROP INDEX IF EXISTS idx_tenant_storage_tenant_id")
    
    # Удаляем таблицу
    op.drop_table('tenant_storage_usage')

