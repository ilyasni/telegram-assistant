"""add post_enrichment kind single index

Context7 best practice: отдельный индекс только для kind с условием WHERE kind IS NOT NULL
для оптимизации запросов, которые фильтруют только по kind без post_id.

Revision ID: 20250122_add_pe_kind_single
Revises: 20251116_add_trend_agents_tables
Create Date: 2025-01-22 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '20250122_add_pe_kind_single'
down_revision = '20251116_trend_agents'  # Context7: Исправлено на последнюю существующую ревизию
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Context7: Создание отдельного индекса только для kind с условием WHERE kind IS NOT NULL.
    
    Это улучшит производительность запросов, которые фильтруют только по kind:
    - WHERE kind = 'vision'
    - WHERE kind IN ('vision', 'crawl')
    - и т.д.
    
    Существующий составной индекс (kind, post_id) остается для запросов с обоими условиями.
    """
    # Context7: Проверяем существование индекса перед созданием
    conn = op.get_bind()
    result = conn.execute(sa.text("""
        SELECT indexname FROM pg_indexes 
        WHERE schemaname = 'public' 
        AND tablename = 'post_enrichment'
        AND indexname = 'idx_pe_kind'
    """))
    existing_index = result.fetchone()
    
    if not existing_index:
        # Context7: Создаем индекс только для kind с условием WHERE kind IS NOT NULL
        # Это соответствует определению в модели database.py
        op.create_index(
            'idx_pe_kind',
            'post_enrichment',
            ['kind'],
            postgresql_where=sa.text('kind IS NOT NULL')
        )


def downgrade() -> None:
    """Удаление индекса idx_pe_kind."""
    op.drop_index('idx_pe_kind', table_name='post_enrichment')

