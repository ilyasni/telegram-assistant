"""migrate legacy vision_labels to data JSONB

Context7 best practice: миграция legacy полей в унифицированную структуру data JSONB
для соответствия новой архитектуре post_enrichment.

Revision ID: 20250122_migrate_vision_labels
Revises: 20250122_add_pe_kind_single
Create Date: 2025-01-22 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = '20250122_migrate_vision_labels'
down_revision = '20250122_add_pe_kind_single'  # Context7: Зависит от предыдущей миграции
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Миграция vision_labels в data->>'labels' для записей kind='vision'.
    
    Идемпотентная миграция: можно запускать несколько раз безопасно.
    Мигрируются только записи, где:
    - vision_labels IS NOT NULL
    - kind = 'vision'
    - data->>'labels' IS NULL или data->'labels' = 'null'::jsonb
    """
    conn = op.get_bind()
    
    # Context7: Подсчитываем количество записей для миграции
    count_result = conn.execute(text("""
        SELECT COUNT(*) as count
        FROM post_enrichment
        WHERE vision_labels IS NOT NULL 
        AND kind = 'vision'
        AND (data->>'labels' IS NULL OR data->'labels' = 'null'::jsonb)
    """))
    count_row = count_result.fetchone()
    records_to_migrate = count_row[0] if count_row else 0
    
    if records_to_migrate == 0:
        print("No records to migrate for vision_labels")
        return
    
    print(f"Migrating {records_to_migrate} records from vision_labels to data->>'labels'")
    
    # Context7: Идемпотентная миграция vision_labels в data JSONB
    # Используем jsonb_set для безопасного обновления JSONB поля
    result = conn.execute(text("""
        UPDATE post_enrichment
        SET data = jsonb_set(
            COALESCE(data, '{}'::jsonb),
            '{labels}',
            vision_labels::jsonb
        )
        WHERE vision_labels IS NOT NULL 
        AND kind = 'vision'
        AND (data->>'labels' IS NULL OR data->'labels' = 'null'::jsonb)
    """))
    
    migrated_count = result.rowcount
    conn.commit()
    
    print(f"Successfully migrated {migrated_count} records")
    
    # Context7: Проверяем результат миграции
    verify_result = conn.execute(text("""
        SELECT COUNT(*) as count
        FROM post_enrichment
        WHERE vision_labels IS NOT NULL 
        AND kind = 'vision'
        AND data->>'labels' IS NOT NULL
    """))
    verify_row = verify_result.fetchone()
    verified_count = verify_row[0] if verify_row else 0
    
    print(f"Verified: {verified_count} records now have data->>'labels' populated")


def downgrade() -> None:
    """
    Context7: Откат миграции - очистка data->>'labels' для записей kind='vision'.
    
    ВНИМАНИЕ: Это не восстанавливает vision_labels из data->>'labels',
    так как данные могут быть изменены после миграции.
    """
    conn = op.get_bind()
    
    # Context7: Очищаем data->>'labels' для записей kind='vision'
    # Это не восстанавливает vision_labels, но позволяет откатить структуру
    result = conn.execute(text("""
        UPDATE post_enrichment
        SET data = data - 'labels'
        WHERE kind = 'vision'
        AND data->>'labels' IS NOT NULL
    """))
    
    cleared_count = result.rowcount
    conn.commit()
    
    print(f"Cleared data->>'labels' for {cleared_count} records")

