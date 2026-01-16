"""add is_generic field to trend_clusters

Revision ID: 20251120_add_is_generic
Revises: 20250202_episodic_memory_dlq
Create Date: 2025-11-20 12:00:00.000000

Context7: Добавление поля is_generic для фильтрации generic/low-quality трендов.
Соответствует SQL миграции supabase/migrations/20251120_add_is_generic_to_trend_clusters.sql
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20251120_add_is_generic"
down_revision = "20250202_episodic_memory_dlq"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Добавление поля is_generic в таблицу trend_clusters."""
    # Context7: Проверяем существование колонки, так как SQL миграция Supabase могла уже добавить её
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('trend_clusters')]
    
    if 'is_generic' not in columns:
        op.add_column(
            "trend_clusters",
            sa.Column("is_generic", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )
    
    # Создание индекса для фильтрации non-generic трендов (partial index для лучшей производительности)
    # Проверяем существование индекса
    indexes = [idx['name'] for idx in inspector.get_indexes('trend_clusters')]
    if 'idx_trend_clusters_is_generic' not in indexes:
        op.create_index(
            "idx_trend_clusters_is_generic",
            "trend_clusters",
            ["is_generic"],
            postgresql_where=sa.text("is_generic = false"),
        )


def downgrade() -> None:
    """Удаление поля is_generic из таблицы trend_clusters."""
    op.drop_index("idx_trend_clusters_is_generic", table_name="trend_clusters")
    op.drop_column("trend_clusters", "is_generic")

